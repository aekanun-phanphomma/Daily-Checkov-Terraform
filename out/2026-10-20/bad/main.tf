resource "google_project_iam_member" "example" {
  project = "example-project"
  role    = "roles/storage.admin"
  member  = "serviceAccount:app-sa@example-project.iam.gserviceaccount.com"
}
