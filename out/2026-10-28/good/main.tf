resource "aws_db_instance" "example" {
  allocated_storage   = 20
  engine              = "mysql"
  instance_class      = "db.t3.micro"
  publicly_accessible = false # FIX: disable public IP address allocation
}
