resource "google_compute_firewall" "bad" {
  name    = "allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"] # FIX: restrict SSH to Identity-Aware Proxy CIDR
}
