"""LLM drafts -> gates judge -> repair loop -> next rule if it still fails.

    python src/generate.py --dir out/today
    LLM_FIXTURE_DIR=tests/fixtures/s3 python src/generate.py --dir out/today --rule CKV_AWS_53

Exit codes: 0 bundle written (or today already handled), 1 nothing passed.
On success out/today/ holds bad/ good/ content.json diagram.json meta.json
card.png; publish.py takes it from there.

Retry policy, and why it looks like this:
  * MAX_REPAIRS is a budget per rule shared by every gate and both calls,
    not a count per gate. A fix for G5 can break G3, so every repair re-runs
    all gates from the top, and a per-gate counter would let one rule loop
    through 2 x 7 repairs. A shared budget makes worst-case cost and runtime
    a fixed number you can read off the constants.
  * After the budget, move to the next candidate (MAX_RULES_PER_RUN total).
    A rule the model cannot get right today is very likely one it cannot get
    right tomorrow either, so the failure issue suggests `enabled: false`.
  * Failures that no repair can fix (UNKNOWN answers, no guideline URL) skip
    the repair budget entirely and go straight to the next rule.
  * The model being unavailable is not the rule's fault -- stop the run
    rather than burn through the catalog.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import (PROMPTS_DIR, REPO_ROOT, CheckInfo, Rule, check_source_code,
                    checkov_checks, entry_for, load_catalog, load_state, log,
                    setup_logging, today, write_json)
from gates import (PROVIDERS, GateFailure, gate_bad_fails, gate_diagram,
                   gate_good_passes, gate_post, gate_safety, gate_schema,
                   gate_terraform, providers_tf, require_guideline)
from llm import LLM, LLMUnavailable
from render import CanvasOverflow, render_card
from select_rule import ordered_candidates

MAX_REPAIRS = 2
MAX_RULES_PER_RUN = 3


def fill(template: str, **values) -> str:
    """{{name}} substitution. Not Jinja/str.format: HCL is full of braces."""
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def terraform_version() -> str:
    proc = subprocess.run(["terraform", "version", "-json"], capture_output=True,
                          text=True, check=False)
    try:
        return json.loads(proc.stdout)["terraform_version"]
    except (json.JSONDecodeError, KeyError):
        return "1.x"


def reset_dir(path: Path) -> None:
    """Empty `path`, keeping the directory itself.

    rmtree-then-mkdir races with file-sync tools (OneDrive here) that still
    hold the old directory handle, and fails with "already exists".
    """
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def write_bundle(bundle: Path, rule: Rule, content: dict) -> None:
    for side in ("bad", "good"):
        d = bundle / side
        reset_dir(d)
        (d / "main.tf").write_text(content[f"{side}_tf"].strip() + "\n", encoding="utf-8")
        (d / "providers.tf").write_text(providers_tf(rule.cloud), encoding="utf-8")


def read_back(bundle: Path, content: dict) -> dict:
    """The model's code after `terraform fmt`, which is what gets published."""
    return {**content,
            "bad_tf": (bundle / "bad" / "main.tf").read_text(encoding="utf-8"),
            "good_tf": (bundle / "good" / "main.tf").read_text(encoding="utf-8")}


class Attempt:
    """One rule's run through the gates, with a shared repair budget."""

    def __init__(self, rule: Rule, info: CheckInfo, llm: LLM, catalog: list[Rule],
                 bundle: Path):
        self.rule, self.info, self.llm = rule, info, llm
        self.catalog, self.bundle = catalog, bundle
        self.repairs_left = MAX_REPAIRS
        self.log: list[dict] = []

    def ask(self, kind: str, text: str, check) -> dict:
        """Call the model, run `check`, repair until it passes or budget ends."""
        # Each call is single-turn, so a repair prompt carries the original
        # task too -- otherwise the model "fixes" the gate and forgets the rule.
        task, label = text, kind
        while True:
            output: dict = {}
            try:
                try:
                    output = self.llm.generate_json(text, label)
                except ValueError as exc:
                    raise GateFailure("G1 schema", str(exc))
                result = check(output)
                self.log.append({"call": label, "ok": True})
                return result
            except GateFailure as failure:
                self.log.append({"call": label, "ok": False, "gate": failure.gate,
                                 "detail": failure.detail[:1500]})
                log.warning("%s %s failed %s", self.rule.id, label, failure)
                if not failure.repairable or self.repairs_left == 0:
                    raise
                self.repairs_left -= 1
                label = f"{kind}/repair-{MAX_REPAIRS - self.repairs_left}"
                text = task + "\n\n" + fill(prompt("repair"), gate_name=failure.gate,
                            tool_output=failure.detail,
                            previous_output=json.dumps(output, indent=1)[:12000])

    def check_content(self, content: dict) -> dict:
        gate_schema(content)
        gate_post(content)
        gate_safety(content["bad_tf"], "bad_tf", self.rule)
        gate_safety(content["good_tf"], "good_tf", self.rule)
        write_bundle(self.bundle, self.rule, content)
        gate_terraform(self.bundle)
        bad = gate_bad_fails(self.bundle, self.rule)
        good = gate_good_passes(self.bundle, self.rule, self.catalog)
        self.guideline = require_guideline(bad, good, rule=self.rule)
        return read_back(self.bundle, content)

    def run(self) -> dict:
        provider = PROVIDERS[self.rule.cloud]
        content = self.ask("content", fill(
            prompt("content"),
            check_id=self.rule.id, check_name=self.info.check_name,
            resource_type=self.rule.resource, cloud=self.rule.cloud,
            source_url=self.info.source_url,
            source_code=check_source_code(self.info.source_url) or "UNAVAILABLE",
            tf_version=terraform_version(),
            provider_source=provider["source"], provider_version=provider["version"],
        ), self.check_content)

        def check_diagram(spec: dict) -> dict:
            gate_diagram(spec, content["bad_tf"], content["good_tf"])
            try:
                render_card(spec, self.rule.to_dict(), self.bundle)     # G8
            except CanvasOverflow as exc:
                raise GateFailure("G8 render", str(exc))
            return spec

        diagram = self.ask("diagram", fill(
            prompt("diagram"), check_id=self.rule.id,
            check_name=self.info.check_name,
            bad_tf=content["bad_tf"], good_tf=content["good_tf"],
        ), check_diagram)

        write_json(self.bundle / "content.json", content)
        write_json(self.bundle / "diagram.json", diagram)
        return {
            "rule": self.rule.to_dict(),
            "check_name": self.info.check_name,
            "source_url": self.info.source_url,
            "guideline_url": self.guideline,
            "model": self.llm.model_used,
            "llm_calls": self.llm.calls,
            "repairs_used": MAX_REPAIRS - self.repairs_left,
            "attempts": self.log,
        }


def github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="out/today")
    parser.add_argument("--rule", help="force one check id (testing)")
    args = parser.parse_args()

    date = today()
    state = load_state()
    dry_run = os.environ.get("DRY_RUN", "") in ("1", "true")
    if entry_for(state, date) and not dry_run and not args.rule:
        # Re-run of a day that already published (or half-published). The
        # publish job resumes from out/<date>/, so there is nothing to make.
        log.info("%s already has a state entry -- nothing to generate", date)
        github_output(generated="false")
        return 0

    catalog = load_catalog()
    checks = checkov_checks()
    queue = ([r for r in catalog if r.id == args.rule] if args.rule
             else ordered_candidates(catalog, state))[:MAX_RULES_PER_RUN]
    if not queue:
        log.error("catalog exhausted: every enabled rule has been posted")
        return 1

    bundle = (REPO_ROOT / args.dir).resolve()
    system = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
    failures = []
    for rule in queue:
        reset_dir(bundle)
        info = checks.get(rule.id)
        if not info or rule.resource not in info.resource_types:
            failures.append({"check_id": rule.id, "reason": "not in `checkov --list` "
                             "for this resource type (catalog drift)"})
            continue
        log.info("trying %s (%s, %s)", rule.id, rule.cloud, rule.resource)
        attempt = Attempt(rule, info, LLM(system, os.environ.get("LLM_FIXTURE_DIR")),
                          catalog, bundle)
        try:
            meta = attempt.run()
        except LLMUnavailable as exc:
            failures.append({"check_id": rule.id, "reason": str(exc)})
            break
        except GateFailure as failure:
            failures.append({"check_id": rule.id, "reason": str(failure),
                             "attempts": attempt.log})
            continue
        meta.update(date=date, skipped_before=failures)
        write_json(bundle / "meta.json", meta)
        log.info("%s passed every gate (%d repair(s))", rule.id, meta["repairs_used"])
        github_output(generated="true", check_id=rule.id)
        return 0

    reset_dir(bundle)
    write_json(bundle / "failures.json", {"date": date, "failures": failures})
    log.error("no rule passed the gates today: %s",
              "; ".join(f"{f['check_id']}: {f['reason'][:120]}" for f in failures))
    return 1


if __name__ == "__main__":
    sys.exit(main())
