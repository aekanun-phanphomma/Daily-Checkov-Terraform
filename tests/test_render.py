import copy
import json
from pathlib import Path

import pytest

import icons
from render import ATTACKER_IPS, ICONS, TYPE_ICON, build_story, pick_icon

DIAGRAM = json.loads((Path(__file__).parent / "fixtures" / "s3" / "diagram.json").read_text())
RULE = {"id": "CKV_AWS_53", "cloud": "aws", "resource": "aws_s3_bucket_public_access_block"}


@pytest.fixture(autouse=True)
def no_vendor_icons(tmp_path, monkeypatch):
    # Rendering decisions must not depend on whether this machine has packs.
    monkeypatch.setattr(icons, "VENDOR_DIR", tmp_path)


def test_gate_shows_the_first_verified_diff_pair():
    story = build_story(DIAGRAM, RULE)
    assert story["gate"]["before"] == "block_public_acls = false"
    assert story["gate"]["after"] == "block_public_acls = true"
    assert story["target"]["icon"] == "bucket" and story["target"]["img"] is None
    # Seen in the first real run: the gate must not borrow the target's icon.
    assert story["gate"]["icon"] == "lock"


def test_log_uses_documentation_ips_and_the_allowed_cidr():
    spec = copy.deepcopy(DIAGRAM)
    spec["ally"] = {"label": "Admin subnet"}
    spec["code_diff"] = ['- cidr_blocks = ["0.0.0.0/0"]', '+ cidr_blocks = ["10.20.30.0/24"]']
    story = build_story(spec, RULE)
    assert story["log_before"][0]["text"].startswith(ATTACKER_IPS[0])
    allowed = [r["text"] for r in story["log_after"] if r["allowed"]]
    assert allowed and all(t.startswith("10.20.30.12") for t in allowed)
    assert story["ally"]["icon"] == "network"


def test_long_diff_gives_up_the_log_before_the_card_overflows():
    spec = copy.deepcopy(DIAGRAM)
    spec["code_diff"] += ["- restrict_public_buckets = false", "+ restrict_public_buckets = true"]
    assert len(build_story(spec, RULE)["log_before"]) == 3
    spec["ally"] = {"label": "Admin role"}
    assert build_story(spec, RULE)["log_before"] == []


@pytest.mark.parametrize("label, resource, expected", [
    ("S3 Bucket", "aws_s3_bucket_public_access_block", "bucket"),
    ("Web SG", "aws_security_group", "firewall"),
    ("Orders DB", "aws_db_instance", "database"),
    ("Key Vault", "azurerm_key_vault", "key"),
    ("GKE cluster", "google_container_cluster", "cluster"),
    ("Function", "aws_lambda_function_url", "function"),
    ("Thing", "google_compute_instance", "server"),
])
def test_resource_icon_is_inferred_from_label_and_type(label, resource, expected):
    assert pick_icon({"type": "resource", "label": label}, resource) == expected


def test_model_icon_wins_and_other_types_use_their_default():
    assert pick_icon({"type": "resource", "label": "x", "icon": "queue"}, "aws_s3_bucket") == "queue"
    assert pick_icon({"type": "internet", "label": "Anyone"}, "aws_s3_bucket") == "globe"
    assert set(TYPE_ICON.values()) <= ICONS


def test_every_catalog_topic_has_a_scene_kit():
    from common import load_catalog
    from render import KIT_OF_TOPIC, KITS
    missing = sorted({r.topic for r in load_catalog()} - set(KIT_OF_TOPIC))
    assert not missing, f"add to KIT_OF_TOPIC: {missing}"
    assert set(KIT_OF_TOPIC.values()) <= set(KITS)


def test_fixes_that_do_not_block_are_not_drawn_as_blocking():
    # Logging records the call; encryption makes the read useless. Neither
    # stops the request, so the after-scene must not show it stopped.
    for topic in ("logging", "encryption-at-rest", "transport-security"):
        assert build_story(DIAGRAM, {**RULE, "topic": topic})["kit"]["blocks"] is False
    assert build_story(DIAGRAM, {**RULE, "topic": "network-ingress"})["kit"]["blocks"] is True


def test_log_column_fits_the_longest_source():
    spec = {**DIAGRAM, "attacker": {"type": "user", "label": "Stolen access key"}}
    row = build_story(spec, RULE)["log_before"][0]["text"]
    assert row.startswith("Stolen access key  ")
