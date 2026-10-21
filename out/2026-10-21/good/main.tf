resource "aws_lambda_function" "example" {
  function_name = "example-function"
  role          = "arn:aws:iam::123456789012:role/example-role"
  handler       = "index.handler"
  runtime       = "nodejs20.x"
  filename      = "lambda.zip"

  environment {
    variables = {
      SECRET_NAME = "example-secret-name" # FIX: store secret reference instead of plaintext key
    }
  }
}
