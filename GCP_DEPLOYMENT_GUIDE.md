# Google Cloud Platform (GCP) Deployment Guide

This guide contains the step-by-step commands to deploy the **Sanjeevani System** and **Sanjeevani Auth** services to Google Cloud Run, backed by a Memorystore Redis instance and MongoDB Atlas (via VPC peering).

## Prerequisites
1. Install the [Google Cloud CLI (gcloud)](https://cloud.google.com/sdk/docs/install)
2. Authenticate: `gcloud auth login`
3. Set your project: `gcloud config set project YOUR_PROJECT_ID`
4. Enable necessary APIs:
   ```bash
   gcloud services enable run.googleapis.com \
                          vpcaccess.googleapis.com \
                          redis.googleapis.com \
                          artifactregistry.googleapis.com
   ```

---

## 1. Create a Serverless VPC Access Connector
Cloud Run instances need this to communicate with Redis and MongoDB Atlas over private IPs.

```bash
gcloud compute networks vpc-access connectors create sanjeevani-vpc-connector \
    --region=asia-south1 \
    --range=10.8.0.0/28 \
    --network=default
```

---

## 2. Provision Redis (Memorystore)
This provides the caching and rate-limiting backend.

```bash
gcloud redis instances create sanjeevani-redis \
    --size=1 \
    --region=asia-south1 \
    --network=default \
    --tier=basic
```

*After creation, get the Redis IP address:*
```bash
gcloud redis instances describe sanjeevani-redis --region=asia-south1 --format="value(host)"
```
*(Save this IP. E.g., `10.x.x.x` - you will set it as `REDIS_URL=redis://10.x.x.x:6379` in Cloud Run).*

---

## 3. Deploy Sanjeevani Auth Service

First, navigate to the Auth directory:
```bash
cd "Sanjeevani Auth"
```

Deploy directly from source (Cloud Build will automatically containerize it based on the Dockerfile):
```bash
gcloud run deploy sanjeevani-auth \
    --source . \
    --region=asia-south1 \
    --allow-unauthenticated \
    --vpc-connector=sanjeevani-vpc-connector \
    --set-env-vars="ENV=production,REDIS_URL=redis://<YOUR_REDIS_IP>:6379,JWT_SECRET=<YOUR_SECRET>,MONGO_URI=<YOUR_ATLAS_URI>"
```

---

## 4. Deploy Sanjeevani System Service

Navigate to the System directory:
```bash
cd "../Sanjeevani System"
```

Deploy to Cloud Run:
```bash
gcloud run deploy sanjeevani-system \
    --source . \
    --region=asia-south1 \
    --allow-unauthenticated \
    --vpc-connector=sanjeevani-vpc-connector \
    --set-env-vars="ENV=production,REDIS_URL=redis://<YOUR_REDIS_IP>:6379,MONGO_URI=<YOUR_ATLAS_URI>,GROQ_API_KEY=<YOUR_KEY>,JWT_SECRET=<YOUR_SECRET>"
```

---

## 5. (Optional but Recommended) Load Balancer
To put both services under a single domain (e.g. `api.sanjeevani.com`), you can create an external HTTP(S) Load Balancer in the GCP console:
1. Create a **Serverless Network Endpoint Group (NEG)** for `sanjeevani-auth` and another for `sanjeevani-system`.
2. Configure **URL Mapping**:
   - `/api/v1/auth/*` -> Auth NEG
   - `/*` -> System NEG
3. Attach a Google-managed SSL certificate for your domain.
