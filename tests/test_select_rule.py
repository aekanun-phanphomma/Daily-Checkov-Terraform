from common import Rule
from select_rule import ordered_candidates


def rule(id, cloud, priority="P1", enabled=True):
    return Rule(id=id, cloud=cloud, resource="r", priority=priority, topic="t",
                enabled=enabled)


CATALOG = [
    rule("A1", "aws"), rule("A2", "aws"), rule("Z1", "azure"), rule("G1", "gcp"),
    rule("A3", "aws", priority="P2"), rule("OFF", "gcp", enabled=False),
]


def ids(rules):
    return [r.id for r in rules]


def test_empty_history_starts_with_aws_p1():
    assert ids(ordered_candidates(CATALOG, {"posts": []})) == ["A1", "A2", "Z1", "G1", "A3"]


def test_rotates_to_cloud_after_last_posted():
    state = {"posts": [{"check_id": "A1", "cloud": "aws"}]}
    assert ids(ordered_candidates(CATALOG, state)) == ["Z1", "G1", "A2", "A3"]


def test_p1_is_exhausted_before_p2():
    state = {"posts": [{"check_id": i, "cloud": c}
                       for i, c in [("A1", "aws"), ("A2", "aws"), ("Z1", "azure")]]}
    assert ids(ordered_candidates(CATALOG, state)) == ["G1", "A3"]


def test_disabled_and_posted_rules_are_never_offered():
    state = {"posts": [{"check_id": "G1", "cloud": "gcp"}]}
    assert "OFF" not in ids(ordered_candidates(CATALOG, state))
    assert "G1" not in ids(ordered_candidates(CATALOG, state))


def test_recent_topic_waits_its_turn_within_the_cloud():
    # Each cloud's first P1 was SSH, so three SSH posts went out in a row.
    catalog = [Rule("Z_SSH", "azure", "r", "P1", "network-ingress"),
               Rule("Z_BLOB", "azure", "r", "P1", "public-exposure"),
               Rule("G_SSH", "gcp", "r", "P1", "network-ingress"),
               Rule("A_SSH", "aws", "r", "P1", "network-ingress")]
    state = {"posts": [{"check_id": "A_SSH", "cloud": "aws"}]}
    # Cloud rotation still wins (azure next), but not SSH again within it.
    assert ids(ordered_candidates(catalog, state))[:2] == ["Z_BLOB", "Z_SSH"]
