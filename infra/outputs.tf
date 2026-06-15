output "bucket_name" {
  value       = google_storage_bucket.snapshots.name
  description = "Snapshot bucket. Seed with `gsutil -m rsync -r ../data/seasons gs://<this>/seasons`."
}
