resource "google_storage_bucket" "example" {
  name                     = "example-secure-data-bucket"
  location                 = "US"
  public_access_prevention = "enforced" # FIX: enforce public access prevention
}
