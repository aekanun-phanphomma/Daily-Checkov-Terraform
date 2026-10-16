import json
from pathlib import Path

from publish import LINKEDIN_TEXT_LIMIT, compose_post, escape_linkedin, linkedin_commentary


def make_bundle(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "s3"
    (tmp_path / "content.json").write_text((fixture / "content.json").read_text())
    (tmp_path / "meta.json").write_text(json.dumps({
        "rule": {"id": "CKV_AWS_53", "cloud": "aws"},
        "source_url": "https://github.com/bridgecrewio/checkov/blob/main/x.py",
        "guideline_url": "https://docs.prismacloud.io/x",
    }))
    return tmp_path


def test_post_carries_tool_sourced_links_and_fits_linkedin(tmp_path):
    text = compose_post(make_bundle(tmp_path), "2026-10-17")
    assert "/tree/main/out/2026-10-17" in text
    assert "Check source: https://github.com/bridgecrewio/checkov/" in text
    assert "Guideline: https://docs.prismacloud.io/x" in text
    assert text.rstrip().endswith("#AWS")
    assert len(linkedin_commentary(text)) <= LINKEDIN_TEXT_LIMIT


def test_linkedin_hashtags_become_entities_and_body_is_escaped():
    out = linkedin_commentary("risk (high)\n\n#Terraform #AWS")
    assert out == "risk \\(high\\)\n\n{hashtag|\\#|Terraform} {hashtag|\\#|AWS}"


def test_escape_handles_backslash_first():
    assert escape_linkedin("a\\_b") == "a\\\\\\_b"
