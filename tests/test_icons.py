"""catalog/icons.yaml stays in step with the catalog; packs are pinned."""

import hashlib
import zipfile

import pytest

import icons
from common import load_catalog


def test_every_catalog_resource_has_an_icon_for_clouds_with_a_pack():
    # A rule added to rules.yaml without an icon would quietly get the
    # neutral fallback; fail at PR time instead.
    config = icons.load()
    missing = sorted({f"{r.cloud}: {r.resource}" for r in load_catalog()
                      if r.cloud in config["packages"]
                      and r.resource not in config["resources"].get(r.cloud, {})})
    assert not missing, f"add to catalog/icons.yaml: {missing}"


def test_mapping_has_no_stale_entries():
    used = {(r.cloud, r.resource) for r in load_catalog()}
    stale = [f"{cloud}: {res}" for cloud, rs in icons.load()["resources"].items()
             for res in rs if (cloud, res) not in used]
    assert not stale


def _fake_pack(tmp_path, monkeypatch):
    pack = tmp_path / "pack.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("root/deep/nested/Target.svg", "<svg>target</svg>")
        archive.writestr("root/deep/Gate.svg", "<svg>gate</svg>")
        archive.writestr("root/Unused.svg", "<svg/>")
    config = {
        "packages": {"aws": {"file": "pack.zip", "root": "root/",
                             "sha256": hashlib.sha256(pack.read_bytes()).hexdigest()}},
        "resources": {"aws": {"aws_security_group": {"icon": "deep/nested/Target.svg",
                                                     "gate": "deep/Gate.svg"}}},
    }
    monkeypatch.setattr(icons, "load", lambda: config)
    monkeypatch.setattr(icons, "VENDOR_DIR", tmp_path / "vendor")
    return pack


def test_extract_takes_only_mapped_files_flat(tmp_path, monkeypatch):
    pack = _fake_pack(tmp_path, monkeypatch)
    assert icons.extract(pack) == "aws"
    assert sorted(p.name for p in (tmp_path / "vendor" / "aws").iterdir()) == ["Gate.svg", "Target.svg"]
    found = icons.for_rule("aws", "aws_security_group")
    assert found["target"].startswith("data:image/svg+xml;base64,")
    assert found["gate"] and found["ally"] is None


def test_pack_with_unpinned_hash_is_refused(tmp_path, monkeypatch):
    pack = _fake_pack(tmp_path, monkeypatch)
    with zipfile.ZipFile(pack, "a") as archive:
        archive.writestr("root/new-release.svg", "<svg/>")
    with pytest.raises(icons.PackMismatch):
        icons.extract(pack)
    assert not (tmp_path / "vendor").exists()


def test_no_pack_means_neutral_icons_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "VENDOR_DIR", tmp_path / "empty")
    assert icons.for_rule("aws", "aws_security_group") == {"target": None, "gate": None, "ally": None}
    assert icons.for_rule("gcp", "google_compute_firewall")["target"] is None
