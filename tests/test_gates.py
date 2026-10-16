import copy
import json
from pathlib import Path

import pytest

from common import Rule
from gates import (GateFailure, gate_diagram, gate_post, gate_safety, gate_schema,
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


def test_after_panel_needs_a_blocked_edge():
    spec = copy.deepcopy(DIAGRAM)
    for edge in spec["after"]["edges"]:
        edge.pop("blocked")
    assert "blocked" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])


def test_edges_must_reference_nodes():
    spec = copy.deepcopy(DIAGRAM)
    spec["before"]["edges"][0]["to"] = "ghost"
    assert "ghost" in fails(gate_diagram, spec, CONTENT["bad_tf"], CONTENT["good_tf"])


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


def test_unknown_icon_is_dropped_not_failed():
    spec = copy.deepcopy(DIAGRAM)
    spec["before"]["nodes"][1]["icon"] = "s3-logo"
    spec["after"]["nodes"][1]["icon"] = "shield"
    gate_diagram(spec, CONTENT["bad_tf"], CONTENT["good_tf"])
    assert "icon" not in spec["before"]["nodes"][1]
    assert spec["after"]["nodes"][1]["icon"] == "shield"


def test_normalize_ignores_alignment_and_comments():
    assert normalize_code_line("  block_public_acls       = true # FIX: x") == \
        "block_public_acls = true"
