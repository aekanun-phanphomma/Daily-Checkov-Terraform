import pytest

from render import ICONS, TYPE_ICON, pick_icon


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
