resource "google_compute_firewall" "bad" {
  name    = "bad-firewall"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["3389"]
  }

  source_ranges = ["0.0.0.0/0"]
}
