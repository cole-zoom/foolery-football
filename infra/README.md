# infra

Terraform owns one thing: the GCS bucket that holds the snapshot data. Everything else (Artifact Registry, Cloud Run service, Cloud Build trigger, runtime SA) is set up through the GCP console — Cloud Run's "Continuously deploy from a repository" flow handles all of it in one wizard.

## One-time bootstrap

```sh
gcloud auth login
gcloud auth application-default login
gcloud config set project <PROJECT_ID>

gcloud services enable \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com
```

## Step 1 — Create the bucket

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in project_id
terraform init
terraform apply
```

State lives locally. Output `bucket_name` is the bucket you'll point the API at.

## Step 2 — Seed the bucket

Grab the bucket name once (run from `infra/`):

```sh
BUCKET=$(terraform output -raw bucket_name)
```

Then upload, **from the repo root**:

```sh
cd ..   # repo root, so data/seasons resolves
gcloud storage rsync -r data/seasons gs://$BUCKET/seasons
```

Past seasons (2023, 2024, 2025) are immutable per the snapshot contract — once seeded, never touched. The 2026 folder gets re-synced whenever you re-run `stats-loader` against the bucket from your laptop.

## Step 3 — Deploy the API via Cloud Run console

In the GCP console: **Cloud Run → Create Service → Continuously deploy from a repository**.

- Repository: this repo, branch `main`.
- Build type: **Dockerfile**.
- Source location: `/services/api/Dockerfile` (path in the repo).
- Build context: `/` (repo root) — the Dockerfile needs all four service packages visible.
- Service name: `ffdm-api` (or whatever).
- Region: same as the bucket.
- Authentication: **Allow unauthenticated invocations**.
- CPU allocation: **CPU is always allocated** (avoids cold-start latency).
- Min instances: `1`. Max: `4`.
- Memory: `512 MiB`. CPU: `1`.

**Environment variables** (under Container → Variables & Secrets):

| Var | Value |
| -- | -- |
| `FFDM_SNAPSHOT_BACKEND` | `gcs` |
| `FFDM_GCS_BUCKET` | the bucket name from `terraform output` |
| `FFDM_CORS_ORIGINS` | comma-joined list of frontend origins |

Optional: `FFDM_SLEEPER_BASE_URL`, `FFDM_HEADSHOT_BASE_URL`, `FFDM_GCS_PREFIX` (default `seasons`).

**Runtime service account**: pick or create one in the wizard (e.g. `ffdm-api@…`).

## Step 4 — Grant the runtime SA read access to the bucket

Every Cloud Run request the API makes to GCS is authenticated as the service's runtime SA. The bucket is locked down by default, so this grant is what lets the API actually load snapshots.

After the Cloud Run service exists, grab the SA email from **Cloud Run → ffdm-api → Security tab** (or the Service details page). Then, from `infra/`:

```sh
SA=<runtime-sa-email>   # e.g. ffdm-api@<project>.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://$(terraform output -raw bucket_name) \
  --member="serviceAccount:$SA" \
  --role=roles/storage.objectViewer
```

One-time. Survives every future deploy. Skip it and the API returns 503 on any snapshot read.

Future pushes to `main` trigger a Cloud Build → fresh image → new Cloud Run revision. No more manual work after this.

## Verify

```sh
curl -fsS $(gcloud run services describe ffdm-api --region <REGION> --format='value(status.url)')/health
```
