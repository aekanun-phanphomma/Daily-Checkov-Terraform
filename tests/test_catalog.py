"""Catalog drift: every entry must exist in the PINNED Checkov.

Runs `checkov --list` (a few seconds). Run it after every Checkov bump --
an ID that was renamed or a resource type that a check stopped covering
fails here instead of at 07:30 on a weekday.
"""

from collections import Counter

from common import checkov_checks, load_catalog
from gates import PROVIDERS, RESOURCE_PREFIX


def test_catalog_ids_are_unique():
    dupes = [i for i, n in Counter(r.id for r in load_catalog()).items() if n > 1]
    assert not dupes


def test_catalog_entries_exist_in_pinned_checkov():
    checks = checkov_checks()
    missing = [f"{r.id} on {r.resource}" for r in load_catalog()
               if r.id not in checks or r.resource not in checks[r.id].resource_types]
    assert not missing, f"not in `checkov --list`: {missing}"


def test_catalog_values_are_known():
    for r in load_catalog():
        assert r.cloud in PROVIDERS
        assert r.priority in ("P1", "P2", "P3")
        assert r.resource.startswith(RESOURCE_PREFIX[r.cloud])
