import generate
from generate import FORMATS, RECENT_POSTS, post_format, prompt


def test_formats_rotate_and_every_format_has_a_prompt():
    seen = [post_format({"posts": [{}] * n}) for n in range(len(FORMATS) * 2)]
    assert seen[:len(FORMATS)] == list(FORMATS) == seen[len(FORMATS):]
    for name in FORMATS:
        text = prompt(f"formats/{name}")
        assert text.startswith("Format:")
        # Every format names the check, so the post can never drop it.
        assert "{{check_id}}" in text


def test_freshness_window_is_shorter_than_a_format_cycle():
    # Otherwise "Myth:" would collide with the myth post one cycle earlier.
    assert RECENT_POSTS < len(FORMATS)


def test_recent_posts_reads_published_bundles_oldest_first(tmp_path, monkeypatch):
    import json
    for name, text in [("2026-10-16", "a"), ("2026-10-16-2", "b"), ("2026-10-17", "c"),
                       ("today", "skip")]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "content.json").write_text(json.dumps({"post_en": text}))
    monkeypatch.setattr(generate, "OUT_DIR", tmp_path)
    assert generate.recent_posts(2) == ["b", "c"]
