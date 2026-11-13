resource "google_project_iam_member" "project_user" {
  project = "my-project-id"
  role    = "roles/compute.viewer" # FIX: Use fine-grained role instead of basic role
  member  = "user:dev@example.com"
}
