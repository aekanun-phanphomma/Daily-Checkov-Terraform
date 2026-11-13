resource "google_project_iam_member" "project_user" {
  project = "my-project-id"
  role    = "roles/editor"
  member  = "user:dev@example.com"
}
