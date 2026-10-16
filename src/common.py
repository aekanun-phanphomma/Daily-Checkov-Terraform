"""Shared paths, dates, catalog/state I/O and Checkov metadata lookups."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger("checkov-daily")

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "catalog" / "rules.yaml"
STATE_PATH = REPO_ROOT / "state" / "posted.json"
PROMPTS_DIR = REPO_ROOT / "prompts"
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = REPO_ROOT / "templates"
OUT_DIR = REPO_ROOT / "out"

# Bangkok is UTC+7 all year and has never observed DST, so a fixed offset is
# correct and avoids depending on tzdata (not bundled with CPython on Windows).
# Every date in this project -- state entries, out/<date>/ folders, "already
# posted today?" -- is a Bangkok date. The cron fires at 00:30 UTC, which is
# the same calendar day in both zones, but a manual run at 20:00 UTC is not.
BANGKOK = timezone(timedelta(hours=7))

PLATFORMS = ("linkedin", "facebook")


def today() -> str:
    return datetime.now(BANGKOK).strftime("%Y-%m-%d")


def setup_logging() -> None:
    # Windows consoles default to a legacy code page; harmless on Linux.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


# --------------------------------------------------------------------------
# Catalog and state
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    id: str
    cloud: str
    resource: str
    priority: str
    topic: str
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def load_catalog(path: Path = CATALOG_PATH) -> list[Rule]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Rule(**entry) for entry in raw]


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"posts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def entry_for(state: dict, date: str) -> dict | None:
    return next((p for p in state["posts"] if p["date"] == date), None)


def entries_for_day(state: dict, date: str) -> list[dict]:
    """Today's entry plus any extra manual posts (keys like 2026-10-16-2)."""
    return [p for p in state["posts"]
            if p["date"] == date or p["date"].startswith(date + "-")]


def extra_posts_allowed() -> bool:
    """ALLOW_EXTRA_POST=1: a manual run may publish a second post today.

    Only the workflow_dispatch input sets it. The extra post gets its own
    key (date-2, date-3, ...), so it has its own out/ folder and its own
    per-platform ids, and every duplicate guard still applies to it.
    """
    return os.environ.get("ALLOW_EXTRA_POST", "") in ("1", "true")


def next_key(state: dict, date: str) -> str:
    if not entry_for(state, date):
        return date
    n = 2
    while entry_for(state, f"{date}-{n}"):
        n += 1
    return f"{date}-{n}"


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Checkov metadata: the "facts" the LLM is allowed to use
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckInfo:
    check_id: str
    check_name: str
    resource_types: tuple[str, ...]
    # Link to the check's own implementation in the Checkov repo. Every check
    # has one in `checkov --list`, which makes it the one source we can always
    # cite -- it is literally the code that decides PASS or FAIL.
    source_url: str


_LIST_ROW = re.compile(
    r"^\|\s*\d+\s*\|\s*(?P<id>CKV2?_[A-Z0-9_]+)\s*\|\s*\w+\s*\|\s*(?P<res>\S+)\s*"
    r"\|\s*(?P<name>.*?)\s*\|\s*Terraform\s*\|\s*(?P<url>\S*)\s*\|\s*$"
)


@lru_cache(maxsize=1)
def checkov_checks() -> dict[str, CheckInfo]:
    """Parse `checkov --list --framework terraform` into {check_id: info}.

    One row per (check, resource type), so rows are merged per id. This runs
    against the installed (pinned) Checkov, which is the version that will
    judge the examples -- a check ID that exists in the docs but not in the
    pinned release is useless to us.
    """
    proc = subprocess.run(
        [*checkov_bin(), "--list", "--framework", "terraform"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    merged: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        match = _LIST_ROW.match(line)
        if not match:
            continue
        info = merged.setdefault(match["id"], {
            "check_name": match["name"], "resources": [], "url": match["url"],
        })
        info["resources"].append(match["res"])
    if not merged:
        raise RuntimeError("`checkov --list` returned no Terraform checks:\n"
                           + proc.stderr[-2000:])
    return {
        cid: CheckInfo(cid, d["check_name"], tuple(d["resources"]), d["url"])
        for cid, d in merged.items()
    }


def checkov_bin() -> list[str]:
    """Run Checkov through the current interpreter, not a PATH lookup.

    That guarantees the pinned version from requirements.txt is the judge
    (not some other checkov on the runner), and works the same on Windows,
    where the venv's extensionless console-script shim cannot be exec'd.
    """
    return [sys.executable, "-m", "checkov.main"]


def check_source_code(source_url: str, limit: int = 6000) -> str:
    """The installed source of a check, looked up from its GitHub URL.

    Handing the model the code that will judge it is the single biggest
    reduction in repair loops: it no longer has to guess which attribute or
    which value the check inspects. Graph checks are listed with a .json URL
    but ship as YAML, so sibling extensions are tried too.
    """
    import checkov

    marker = "/blob/main/"
    if marker not in source_url:
        return ""
    rel = Path(source_url.split(marker, 1)[1])
    base = Path(checkov.__file__).resolve().parent.parent
    for candidate in (rel, rel.with_suffix(".yaml"), rel.with_suffix(".yml"),
                      rel.with_suffix(".json"), rel.with_suffix(".py")):
        path = base / candidate
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
    return ""
