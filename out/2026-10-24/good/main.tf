data "aws_iam_policy_document" "example" {
  statement {
    actions   = ["s3:GetObject"] # FIX: replace wildcard action with specific API
    resources = ["*"]
  }
}
