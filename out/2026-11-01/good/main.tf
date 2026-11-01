resource "aws_kms_key" "example" {
  description = "Example KMS key"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::123456789012:root" # FIX: restrict principal to specific AWS account
        }
        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })
}
