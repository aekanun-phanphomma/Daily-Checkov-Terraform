"""Pick today's rule(s): catalog + posting history -> ordered candidates.

Run directly to preview the queue:  python src/select_rule.py
"""

from __future__ import annotations

import argparse

from common import Rule, load_catalog, load_state, setup_logging

CLOUD_ORDER = ("aws", "azure", "gcp")
PRIORITY_ORDER = ("P1", "P2", "P3")
# How many recent posts a topic has to sit out. Three SSH posts in a row
# (AZURE_10, GCP_2, AWS_24) happened because every cloud's first P1 rule
# is SSH: the cloud rotated, the topic did not.
RECENT_TOPICS = 3


def ordered_candidates(catalog: list[Rule], state: dict) -> list[Rule]:
    """Every enabled, never-posted rule, best first.

    Sort key, in order:
      1. priority      -- P1 is exhausted before any P2 is posted
      2. cloud offset  -- start with the cloud after the last one posted, so
                          a week reads AWS, Azure, GCP, AWS... instead of ten
                          AWS posts in a row (the catalog is AWS-heavy)
      3. fresh topic   -- within that cloud, a topic none of the last
                          RECENT_TOPICS posts covered goes first
      4. catalog order -- the curator's ordering within a cloud and priority

    A list, not a single rule, because the generator falls through to the
    next candidate when one cannot be made to pass the gates.
    """
    posted = {p["check_id"] for p in state["posts"]}
    last_cloud = state["posts"][-1]["cloud"] if state["posts"] else None
    start = (CLOUD_ORDER.index(last_cloud) + 1) if last_cloud in CLOUD_ORDER else 0
    # State predates topics, so look them up in the catalog by check id.
    topic_of = {r.id: r.topic for r in catalog}
    recent = {topic_of.get(p["check_id"]) for p in state["posts"][-RECENT_TOPICS:]}

    def key(indexed: tuple[int, Rule]):
        index, rule = indexed
        offset = (CLOUD_ORDER.index(rule.cloud) - start) % len(CLOUD_ORDER)
        return (PRIORITY_ORDER.index(rule.priority), offset, rule.topic in recent, index)

    remaining = [(i, r) for i, r in enumerate(catalog)
                 if r.enabled and r.id not in posted]
    return [rule for _, rule in sorted(remaining, key=key)]


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    queue = ordered_candidates(load_catalog(), load_state())
    print(f"{len(queue)} rule(s) left in the catalog")
    for rule in queue[:args.limit]:
        print(f"  {rule.priority}  {rule.cloud:<5}  {rule.id:<14} {rule.resource}")


if __name__ == "__main__":
    main()
