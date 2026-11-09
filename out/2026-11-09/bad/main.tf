resource "google_storage_bucket" "example" {
  name     = "example-storage-bucket"
  location = "US"
}

resource "google_storage_bucket_iam_member" "member" {
  bucket = google_storage_bucket.example.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
