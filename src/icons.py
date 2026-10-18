"""Official cloud icons: verify a vendor pack, extract only what we draw.

    python src/icons.py fetch                 # CI: download from *_ICONS_URL
    python src/icons.py extract <zip>...      # local: packs you downloaded

catalog/icons.yaml maps each Terraform resource type to a file inside the
vendor's icon pack. Packs are never committed (see that file for why), so
a pack is accepted only if its sha256 matches the pinned one: the same
pack always yields the same pixels, and a URL that starts serving a new
release cannot silently change the picture.

Files are written flat to icons/vendor/<cloud>/<basename>. Flat on
purpose: the packs nest deep enough to pass Windows' 260-character path
limit, which Python there cannot open.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

import yaml

from common import REPO_ROOT, log, setup_logging

ICONS_PATH = REPO_ROOT / "catalog" / "icons.yaml"
VENDOR_DIR = REPO_ROOT / "icons" / "vendor"


@lru_cache(maxsize=1)
def load() -> dict:
    return yaml.safe_load(ICONS_PATH.read_text(encoding="utf-8"))


def wanted(cloud: str) -> set[str]:
    """Every file of this cloud's pack that the mapping or extras use."""
    config = load()
    files = {path for entry in config["resources"].get(cloud, {}).values()
             for path in entry.values()}
    return files | set(config["packages"][cloud].get("extras", {}).values())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class PackMismatch(RuntimeError):
    pass


def extract(pack: Path, cloud: str | None = None) -> str:
    """Verify `pack` against the pinned hashes and extract the mapped files.

    Returns the cloud it belongs to. A pack whose hash matches no entry is
    refused: either the vendor shipped a new release (re-pin it on purpose)
    or the download is not what it claims to be.
    """
    digest = sha256(pack)
    packages = load()["packages"]
    matches = [c for c, p in packages.items() if p["sha256"] == digest]
    if not matches or (cloud and cloud not in matches):
        raise PackMismatch(f"{pack.name}: sha256 {digest} matches no pinned pack "
                           f"in {ICONS_PATH.name}; re-pin it there if the new release is wanted")
    cloud = matches[0]
    root = packages[cloud].get("root", "")
    out = VENDOR_DIR / cloud
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack) as archive:
        for path in sorted(wanted(cloud)):
            # Only names from our own mapping are read, and only basenames are
            # written, so a crafted member name cannot escape VENDOR_DIR.
            data = archive.read(root + path)
            (out / Path(path).name).write_bytes(data)
    log.info("icons: %s -> %d files in %s", pack.name, len(wanted(cloud)), out)
    return cloud


def fetch() -> None:
    """CI entry point. A missing or mismatching pack is a warning, not an error."""
    import requests

    for cloud in load()["packages"]:
        url = os.environ.get(f"{cloud.upper()}_ICONS_URL", "").strip()
        if not url:
            log.info("icons: %s_ICONS_URL not set, %s uses the neutral icons", cloud.upper(), cloud)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / (url.rsplit("/", 1)[-1].split("?")[0] or "pack.zip")
            try:
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                pack.write_bytes(response.content)
                extract(pack, cloud)
            except (requests.RequestException, PackMismatch, KeyError, zipfile.BadZipFile) as exc:
                # GitHub annotation: visible on the run page without failing it.
                print(f"::warning::{cloud} icons unavailable, using neutral icons: {exc}")


def _uri(cloud: str, path: str | None) -> str | None:
    if not path:
        return None
    file = VENDOR_DIR / cloud / Path(path).name
    if not file.is_file():
        return None
    # A data URI rendered as <img>, not inline <svg>: every vendor SVG keeps
    # its own gradient ids instead of colliding with the others in one page.
    return "data:image/svg+xml;base64," + base64.b64encode(file.read_bytes()).decode()


def for_rule(cloud: str, resource: str, ally_label: str | None = None) -> dict:
    """Data URIs for this rule's target, gate and ally, or None for each
    one without a vendor icon (the template then draws its own)."""
    config = load()
    entry = config["resources"].get(cloud, {}).get(resource, {})
    extras = config["packages"].get(cloud, {}).get("extras", {})
    ally = None
    if ally_label and re.search(r"subnet|network|vnet|vpc|cidr", ally_label, re.I):
        ally = _uri(cloud, extras.get("subnet"))
    return {"target": _uri(cloud, entry.get("icon")),
            "gate": _uri(cloud, entry.get("gate")),
            "ally": ally}


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    ext = sub.add_parser("extract")
    ext.add_argument("packs", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch()
        return
    for pack in args.packs:
        try:
            extract(pack)
        except PackMismatch as exc:
            sys.exit(str(exc))


if __name__ == "__main__":
    main()
