variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for the snapshot bucket."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix applied to bucket name."
  type        = string
  default     = "ffdm"
}
