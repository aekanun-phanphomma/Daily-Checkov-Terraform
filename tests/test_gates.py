import copy
import json
from pathlib import Path

import pytest

from common import Rule
from gates import (GateFailure, gate_diagram, gate_fresh, gate_post, gate_safety, gate_schema,
                   normalize_code_line)

FIXTURE = Path(__file__).parent / "fixtures" / "s3"
CONTENT = json.loads((FIXTURE / "content.json").read_text())
DIAGRAM = json.loads((FIXTURE / "diagram.json").read_text())
RULE = Rule("CKV_AWS_53", "aws", "aws_s3_bucket_public_access_block", "P1", "x")


def fails(gate, *args, repairable=True):
    with pytest.raises(GateFailure) as info:
        gate(*args)
    assert info.value.repairable is repairable
    return info.value.detail


def test_fixture_passes_static_gates():
    gate_schema(CONTENT)
    gate_post(CONTENT)
    gate_safety(CONTENT["bad_tf"], "bad_tf", RULE)
    gate_safety(CONTENT["good_tf"], "good_tf", RULE)
    gate_diagram(DIAGRAM, CONTENT["bad_tf"], CONTENT["good_tf"])


def test_schema_rejects_missing_key():
    broken = {k: v for k, v in CONTENT.items() if k != "post_en"}
    assert "post_en" in fails(gate_schema, broken)


def test_unknown_is_not_repairable():
    broken = {**CONTENT, "why_it_matters": ["UNKNOWN because no context given"] * 3}
    fails(gate_schema, broken, repairable=False)


@pytest.mark.parametrize("snippet, expected", [
    ('module "x" {\n  source = "git::https://example.com/x"\n}\n', "module"),
    ('provider "aws" {\n  region = "us-east-1"\n}\n', "provider"),
    ('data "external" "x" {\n  program = ["sh"]\n}\n', "external"),
    ('resource "null_resource" "x" {}\n', "null_resource"),
    ('resource "aws_instance" "x" {\n  ami = "a"\n  provisioner "local-exec" {\n'
     '    command = "id"\n  }\n}\n', "provisioner"),
])
def test_safety_rejects_untrusted_constructs(snippet, expected):
    assert expected in fails(gate_safety, CONTENT["bad_tf"] + snippet, "bad_tf", RULE)


def test_safety_requires_the_rule_resource():
    tf = 'resource "aws_s3_bucket" "data" {\n  bucket = "x"\n}\n'
    assert "aws_s3_bucket_public_access_block" in fails(gate_safety, tf, "bad_tf", RULE)


def test_safety_reports_hcl_syntax_errors():
    assert "does not parse" in fails(gate_safety, 'resource "aws_s3_bucket" {', "bad_tf", RULE)


@pytest.mark.parametrize("extra, expected", [
    (" See https://example.com", "URL"),
    (" #Terraform", "hashtag"),
    (" ทดสอบ", "Thai"),
    (" \U0001F680", "emoji"),
])
def test_post_must_be_plain_english(extra, expected):
    assert expected in fails(gate_post, {**CONTENT, "post_en": CONTENT["post_en"] + extra})


def test_code_diff_must_come_from_the_code():
    spec = copy.deepcopy(DIAGRAM)
    spec["code_diff"][1] = "+ block_public_acls = maybe"
    assert "does not appear in good_tf" in fails(
        gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])


def test_roles_must_be_different_things():
    # The old card drew "NSG Rule -> NSG Rule": one thing guarding itself.
    spec = copy.deepcopy(DIAGRAM)
    spec["target"]["label"] = "public  ACCESS block"
    assert "same label" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])
    spec = copy.deepcopy(DIAGRAM)
    spec["ally"] = {"label": "Anonymous client"}
    assert "same label" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])


def test_spec_is_one_path_not_a_graph():
    # Free-form nodes/edges let the model draw pictures the story template
    # cannot tell; the schema now only has the roles the card draws.
    spec = copy.deepcopy(DIAGRAM)
    spec.pop("gate")
    assert "gate" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])
    spec = copy.deepcopy(DIAGRAM)
    spec["attacker"]["type"] = "control"
    assert "control" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])


def test_extra_keys_and_aligned_diff_lines_are_tidied_not_failed():
    # Both cost a repair each on the first real run.
    spec = copy.deepcopy(DIAGRAM)
    spec["metadata"] = {"model": "x"}
    spec["code_diff"][0] = "-   block_public_acls       = false"
    gate_diagram(spec, CONTENT["bad_tf"], CONTENT["good_tf"])
    assert "metadata" not in spec
    assert spec["code_diff"][0] == "- block_public_acls = false"
    content = {**CONTENT, "notes": "extra"}
    gate_schema(content)
    assert "notes" not in content


def test_normalize_ignores_alignment_and_comments():
    assert normalize_code_line("  block_public_acls       = true # FIX: x") == \
        "block_public_acls = true"


# G6b: the seams that made the first posts read alike.
RECENT = [
    "Leaving SSH exposed to the public internet creates a vector.\n\nBody.\n\n"
    "How do you currently verify management ports are private?",
]


@pytest.mark.parametrize("post, expected", [
    ("Exposing port 22 invites attacks.\n\nBody text here.", "stock word"),
    ("Port 22 was open.\n\nCheckov check CKV_AWS_24 evaluates security groups.", "formula"),
    ("Leaving nothing to chance.\n\nBody.", "stock word"),
    ("Port 22 was open.\n\nBody.\n\nHow do you currently audit this?", "closing"),
])
def test_post_must_not_reuse_recent_seams(post, expected):
    assert expected in fails(gate_fresh, post, RECENT)


def test_first_word_must_differ_from_recent_openings():
    recent = ["Port 22 sat open on a test VM.\n\nBody.\n\nWould CI catch it?"]
    assert "first word" in fails(gate_fresh, "Port 3389 too.\n\nBody.\n\nThoughts?", recent)
    gate_fresh("One wildcard did it.\n\nBody.\n\nThoughts?", recent)


def test_fresh_post_passes_with_and_without_history():
    post = "One wildcard in source_address_prefix.\n\nStep 1: a scan.\n\nWould your pipeline stop it?"
    gate_fresh(post, RECENT)
    gate_fresh(post, [])
