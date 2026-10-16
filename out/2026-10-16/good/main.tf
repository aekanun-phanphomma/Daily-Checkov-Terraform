resource "aws_s3_bucket" "example" {
  bucket = "example-bucket-name"
}

resource "aws_s3_bucket_public_access_block" "example" {
  bucket                  = aws_s3_bucket.example.id
  block_public_acls       = true # FIX: Enable blocking of public ACLs
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
