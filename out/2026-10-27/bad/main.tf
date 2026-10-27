resource "google_sql_database_instance" "example" {
  name             = "example-database"
  database_version = "SQLSERVER_2019_STANDARD"
  region           = "us-central1"
  settings {
    tier = "db-custom-2-7680"
    ip_configuration {
      ipv4_enabled = true
    }
  }
}
