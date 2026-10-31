resource "google_compute_firewall" "bad" {
  name    = "bad-firewall"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["3389"]
  }

  source_ranges = ["10.0.0.0/8"] # FIX: restrict RDP ingress to internal network
}
