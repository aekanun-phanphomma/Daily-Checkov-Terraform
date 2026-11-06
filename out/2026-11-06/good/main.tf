resource "google_cloud_run_service_iam_member" "invoker" {
  location = "us-central1"
  service  = "example-service"
  role     = "roles/run.invoker"
  member   = "serviceAccount:my-sa@example-project.iam.gserviceaccount.com" # FIX: restrict member to an explicit service account
}
