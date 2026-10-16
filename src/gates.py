"""The correctness gates. Nothing is published unless every one passes.

The LLM only drafts; these functions decide. Each gate raises GateFailure
with the verbatim tool output, which generate.py feeds back to the model as
a repair prompt. A failure the model cannot fix (the check has no guideline
URL, say) is raised with repairable=False so the generator moves straight on
to the next rule instead of burning a repair on it.

  G1  schema        content JSON matches schemas/content.schema.json
  G2  safety        HCL parses; no module/provisioner/external data/etc.
  G3  terraform     fmt + init -backend=false + validate, in a temp copy
  G4  bad fails     checkov on bad/ reports <ID> in failed_checks
  G5  good passes   checkov on good/: <ID> passed, and no P1 check for the
                    same resource type failed; guideline URL present
  G6  post text     English, plain, no URLs/hashtags/emoji, length bounds
  G7  diagram       schema, ids consistent, code_diff copied from the code
  G8  render        the spec fits the 1200x1500 card (in render.py)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from common import SCHEMAS_DIR, Rule, checkov_bin, log

# Pinned per cloud and written by us, never by the model (see the system
# prompt). Two reasons: `terraform validate` on azurerm fails without the
# `features {}` block, and an LLM-written `terraform {}` block is one more
# place for a hallucinated version constraint. The same file ships in the
# published bundle so a reader's `terraform validate` matches ours.
PROVIDERS: dict[str, dict[str, str]] = {
    "aws": {
        "name": "aws", "source": "hashicorp/aws", "version": "~> 6.0",
        "block": 'provider "aws" {\n  region = "us-east-1"\n}\n',
    },
    "azure": {
        "name": "azurerm", "source": "hashicorp/azurerm", "version": "~> 4.0",
        "block": 'provider "azurerm" {\n  features {}\n}\n',
    },
    "gcp": {
        "name": "google", "source": "hashicorp/google", "version": "~> 7.0",
        "block": ('provider "google" {\n  project = "example-project"\n'
                  '  region  = "us-central1"\n}\n'),
    },
}
RESOURCE_PREFIX = {"aws": "aws_", "azure": "azurerm_", "gcp": "google_"}

TOOL_TIMEOUT = 300           # seconds; init downloads a ~100-600 MB provider
MAX_TOOL_OUTPUT = 4000       # chars of tool output handed back to the model

THAI = re.compile(r"[฀-๿]")
EMOJI = re.compile(r"[☀-➿\U0001F000-\U0001FAFF]")
URL = re.compile(r"https?://|www\.", re.I)
HASHTAG = re.compile(r"(^|\s)#\w")


@dataclass
class GateFailure(Exception):
    gate: str
    detail: str
    repairable: bool = True

    def __str__(self) -> str:
        return f"{self.gate}: {self.detail[:300]}"


def providers_tf(cloud: str) -> str:
    p = PROVIDERS[cloud]
    return (
        "terraform {\n  required_providers {\n"
        f"    {p['name']} = {{\n      source  = \"{p['source']}\"\n"
        f"      version = \"{p['version']}\"\n    }}\n  }}\n}}\n\n{p['block']}"
    )


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TOOL_TIMEOUT, env=env, check=False,
    )


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_TOOL_OUTPUT else "...\n" + text[-MAX_TOOL_OUTPUT:]


# --------------------------------------------------------------------------
# G1 schema
# --------------------------------------------------------------------------

def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def check_schema(data: dict, schema_name: str, gate: str) -> None:
    validator = jsonschema.Draft202012Validator(_schema(schema_name))
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "\n".join(
            f"- at {'/'.join(map(str, e.path)) or '(root)'}: {e.message[:200]}"
            for e in errors[:10])
        raise GateFailure(gate, detail)


def drop_extra_keys(data: dict, schema_name: str) -> None:
    """Remove top-level keys the schema does not define, in place.

    Smaller models like to add a "metadata" or "notes" key. That is noise,
    not an error worth a repair round trip; missing or malformed keys still
    fail the schema.
    """
    known = _schema(schema_name)["properties"].keys()
    for key in [k for k in data if k not in known]:
        log.info("dropping unexpected key %r from model output", key)
        del data[key]


def gate_schema(content: dict) -> None:
    drop_extra_keys(content, "content.schema.json")
    check_schema(content, "content.schema.json", "G1 schema")
    unknown = [k for k, v in content.items() if "UNKNOWN" in json.dumps(v)]
    if unknown:
        # The model followed the rules and told us it lacks a fact. Retrying
        # the same rule with the same context will not produce that fact.
        raise GateFailure("G1 schema", f"model answered UNKNOWN in {unknown}",
                          repairable=False)


# --------------------------------------------------------------------------
# G2 safety: the model's HCL is about to be fed to `terraform init`
# --------------------------------------------------------------------------

def gate_safety(tf: str, label: str, rule: Rule) -> None:
    """Static allowlist check before any tool touches the code.

    `terraform init` downloads whatever the configuration asks for. A module
    with a git source, a third-party provider, a provisioner or an external
    data source in model output would mean running untrusted code on the
    runner, so they are rejected here -- before init, not after.
    """
    import hcl2

    gate = "G2 safety"
    try:
        parsed = hcl2.loads(tf)
    except Exception as exc:                  # lark raises several types
        raise GateFailure(gate, f"{label}: HCL does not parse: {exc}")

    forbidden = {"module", "terraform", "provider"} & parsed.keys()
    if forbidden:
        raise GateFailure(gate, f"{label}: remove the {sorted(forbidden)} "
                          "block(s); the pipeline supplies providers.tf")

    prefix = RESOURCE_PREFIX[rule.cloud]
    types: set[str] = set()
    for kind in ("resource", "data"):
        for block in parsed.get(kind, []):
            for rtype, instances in block.items():
                types.add(rtype)
                if not rtype.startswith(prefix):
                    raise GateFailure(gate, f"{label}: {kind} type {rtype!r} is not "
                                      f"allowed; use only {prefix}* types")
                if "provisioner" in json.dumps(instances):
                    raise GateFailure(gate, f"{label}: provisioners are not allowed")
    if rule.resource not in types:
        raise GateFailure(gate, f"{label}: must declare a {rule.resource!r} block "
                          f"(found: {sorted(types) or 'none'})")


# --------------------------------------------------------------------------
# G3 terraform fmt / init / validate
# --------------------------------------------------------------------------

def gate_terraform(bundle: Path) -> None:
    """Validate bad/ and good/ in a scratch copy, then format the originals.

    init runs in a temp copy so `.terraform/` (hundreds of MB of provider
    binaries) and the lock file never land in out/ -- they would otherwise be
    uploaded as an artifact and committed to the public repo every day.
    TF_PLUGIN_CACHE_DIR makes the second init (and every repair) reuse the
    provider downloaded by the first.
    """
    env = dict(os.environ)
    cache = Path(tempfile.gettempdir()) / "checkov-daily-plugin-cache"
    cache.mkdir(exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    env["TF_IN_AUTOMATION"] = "1"

    for side in ("bad", "good"):
        src = bundle / side
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / side
            shutil.copytree(src, work)
            for cmd in (["terraform", "init", "-backend=false", "-input=false", "-no-color"],
                        ["terraform", "validate", "-no-color"]):
                proc = _run(cmd, work, env)
                if proc.returncode != 0:
                    raise GateFailure("G3 terraform",
                                      f"{side}/: `{' '.join(cmd[:2])}` failed:\n"
                                      + _tail(proc.stdout + proc.stderr))
        # Formatting is fixed, not failed: a misaligned `=` is not worth a
        # repair round trip. The published code is always fmt-clean.
        proc = _run(["terraform", "fmt", "-no-color", "."], src)
        if proc.returncode != 0:
            raise GateFailure("G3 terraform", f"{side}/: terraform fmt failed:\n"
                              + _tail(proc.stdout + proc.stderr))


# --------------------------------------------------------------------------
# G4 / G5 checkov
# --------------------------------------------------------------------------

@dataclass
class CheckovResult:
    passed: set[str]
    failed: set[str]
    guidelines: dict[str, str]
    raw: str


def run_checkov(directory: Path, check_ids: list[str]) -> CheckovResult:
    """`checkov -d DIR --check IDS -o json`, parsed.

    The exit code is ignored on purpose: it is 1 whenever anything fails,
    which for bad/ is the expected outcome. The JSON is the only signal.
    It is a dict for a single framework, a list for several, and a bare
    summary (no "results") when nothing was scanned -- all three handled.
    """
    proc = _run([*checkov_bin(), "-d", ".", "--framework", "terraform",
                 "--check", ",".join(check_ids), "-o", "json", "--compact"],
                directory)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise GateFailure("checkov", "checkov did not return JSON:\n"
                          + _tail(proc.stdout + proc.stderr), repairable=False)
    reports = report if isinstance(report, list) else [report]
    passed, failed, guidelines = set(), set(), {}
    for rep in reports:
        results = rep.get("results", {})
        for check in results.get("passed_checks", []):
            passed.add(check["check_id"])
            if check.get("guideline"):
                guidelines[check["check_id"]] = check["guideline"]
        for check in results.get("failed_checks", []):
            failed.add(check["check_id"])
            if check.get("guideline"):
                guidelines[check["check_id"]] = check["guideline"]
    summary = json.dumps([
        {"check_id": c["check_id"], "result": c["check_result"]["result"],
         "resource": c.get("resource"),
         "evaluated_keys": c["check_result"].get("evaluated_keys")}
        for rep in reports
        for key in ("passed_checks", "failed_checks")
        for c in rep.get("results", {}).get(key, [])
    ], indent=1)
    return CheckovResult(passed, failed, guidelines, summary)


def gate_bad_fails(bundle: Path, rule: Rule) -> CheckovResult:
    result = run_checkov(bundle / "bad", [rule.id])
    if rule.id not in result.failed:
        raise GateFailure("G4 bad must FAIL",
                          f"checkov did not report {rule.id} as failed for bad_tf. "
                          f"Results:\n{result.raw}")
    return result


def gate_good_passes(bundle: Path, rule: Rule, catalog: list[Rule]) -> CheckovResult:
    # "Clean" means: the fix does not trip any other P1 rule we teach for the
    # same resource type. Not "passes every Checkov check" -- most minimal
    # examples fail a dozen unrelated checks (tags, logging, KMS), and the
    # post says it focuses on one rule.
    siblings = sorted({r.id for r in catalog
                       if r.resource == rule.resource and r.priority == "P1"} | {rule.id})
    result = run_checkov(bundle / "good", siblings)
    if rule.id not in result.passed or result.failed:
        raise GateFailure("G5 good must PASS",
                          f"expected {rule.id} passed and none of {siblings} failed. "
                          f"Results:\n{result.raw}")
    return result


def require_guideline(*results: CheckovResult, rule: Rule) -> str:
    for result in results:
        url = result.guidelines.get(rule.id, "")
        if url.startswith("https://"):
            return url
    raise GateFailure("G5 good must PASS",
                      f"Checkov returned no guideline URL for {rule.id}; every "
                      "post must cite one", repairable=False)


# --------------------------------------------------------------------------
# G6 post text
# --------------------------------------------------------------------------

def _plain_english(text: str, where: str, gate: str) -> None:
    for pattern, what in ((THAI, "Thai characters"), (EMOJI, "emoji"),
                          (URL, "a URL"), (HASHTAG, "a hashtag")):
        if pattern.search(text):
            raise GateFailure(gate, f"{where} contains {what}; posts are plain "
                              "English and the pipeline adds links and hashtags")


def gate_post(content: dict) -> None:
    gate = "G6 post text"
    _plain_english(content["post_en"], "post_en", gate)
    if "```" in content["post_en"] or "resource \"" in content["post_en"]:
        raise GateFailure(gate, "post_en contains code; code lives in the image")
    for i, line in enumerate(content["why_it_matters"]):
        _plain_english(line, f"why_it_matters[{i}]", gate)


# --------------------------------------------------------------------------
# G7 diagram spec
# --------------------------------------------------------------------------

def normalize_code_line(line: str) -> str:
    """Strip a trailing `# comment` and collapse runs of whitespace.

    `terraform fmt` aligns `=` across a block, so `block_public_acls = true`
    in a diff line never matches the formatted code character for character.
    """
    line = re.sub(r"\s+#.*$", "", line)
    return " ".join(line.split())


def gate_diagram(spec: dict, bad_tf: str, good_tf: str) -> None:
    gate = "G7 diagram"
    drop_extra_keys(spec, "diagram.schema.json")
    # Models copy diff lines with fmt's alignment ("-   x       = 1"). The
    # comparison below ignores whitespace anyway, so normalise first rather
    # than spend a repair on it -- and the card shows "- x = 1", not columns.
    spec["code_diff"] = [
        f"{line[0]} {normalize_code_line(line[1:])}"
        if isinstance(line, str) and line[:1] in "-+" and line[1:].strip() else line
        for line in spec.get("code_diff", [])
    ]
    # An unknown icon only costs the picture its hint (render.py infers one),
    # so it is dropped rather than repaired.
    allowed = _schema("diagram.schema.json")["$defs"]["node"]["properties"]["icon"]["enum"]
    for panel in ("before", "after"):
        for node in spec.get(panel, {}).get("nodes", []):
            if isinstance(node, dict) and "icon" in node and node["icon"] not in allowed:
                del node["icon"]
    check_schema(spec, "diagram.schema.json", gate)
    _plain_english(json.dumps(spec, ensure_ascii=False), "diagram_spec", gate)

    for panel in ("before", "after"):
        ids = [n["id"] for n in spec[panel]["nodes"]]
        if len(ids) != len(set(ids)):
            raise GateFailure(gate, f"{panel}: node ids are not unique: {ids}")
        for edge in spec[panel]["edges"]:
            for end in ("from", "to"):
                if edge[end] not in ids:
                    raise GateFailure(gate, f"{panel}: edge {end}={edge[end]!r} "
                                      f"is not a node id {ids}")
    if not any(e.get("blocked") for e in spec["after"]["edges"]):
        raise GateFailure(gate, "after: at least one edge must set blocked: true")

    code = {"-": {normalize_code_line(l) for l in bad_tf.splitlines()},
            "+": {normalize_code_line(l) for l in good_tf.splitlines()}}
    signs = set()
    for line in spec["code_diff"]:
        sign, body = line[0], normalize_code_line(line[2:])
        signs.add(sign)
        if body not in code[sign]:
            side = "bad_tf" if sign == "-" else "good_tf"
            raise GateFailure(gate, f"code_diff line {line!r} does not appear in {side}")
    if signs != {"-", "+"}:
        raise GateFailure(gate, "code_diff needs at least one '-' and one '+' line")
