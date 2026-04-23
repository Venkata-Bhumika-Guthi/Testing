# LLM Health Guardian

**LLM Health Guardian** is a production-style observability and incident-response system for LLM applications powered by **Google Vertex AI / Gemini**, with end-to-end monitoring built on **Datadog**.

It is designed to treat LLM workloads like real production systems: measurable, monitorable, and operationally safe — not a black box.

---

## Why this exists

Traditional observability tells you when servers are up — but LLM apps fail in new ways:

- Latency can degrade silently (especially tail latency)
- Errors can spike due to upstream model issues or misconfiguration
- Token usage can explode (leading to cost spikes)
- Prompts can become unsafe or blocked
- Small performance regressions can become large UX failures

LLM Health Guardian makes these behaviors visible and actionable.

---

## Key Features

### 1) LLM-aware telemetry (first-class signals)
Emits structured telemetry for each `/api/chat` request:
- **Latency (ms)**
- **Tokens in / tokens out / total tokens**
- **Estimated cost (USD)**
- **Success vs error counters**
- Optional safety / blocked signals

### 2) Datadog dashboard: single pane of glass
A unified “LLM Health Guardian – Prod” dashboard surfaces:
- Requests per minute
- p95 latency (tail latency)
- Error rate
- Token usage trends
- Cost trends
- Live logs for rapid triage
- Composite health scoring (optional)

### 3) Detection rules (Monitors)
Includes monitors for:
- **Error-rate spike**
- **Latency regression (p95)**
- **Token anomaly**
- **Cost anomaly**
- **Safety blocks**

### 4) Actionable incident response
At least one monitor is wired to **Datadog Incident Management** so that when a detection rule triggers, Datadog creates an actionable record with context and triage steps.

---

## Architecture

**Client** → **FastAPI /api/chat** → **Vertex AI (Gemini)**  
→ emits **Logs + Metrics** → **Datadog**  
→ **Monitors** → **Incident Management**  
→ Dashboard provides real-time service health visibility

---

## Tech Stack

- **Python**, **FastAPI**, **Uvicorn**, **Pydantic**
- **Google Cloud Run** (hosting)
- **Google Vertex AI / Gemini** (LLM)
- **Datadog** (Logs, Metrics, Dashboards, Monitors, Incident Management)
- Docker (container packaging)

---

## Live Demo

- Hosted API (Cloud Run):  
  `https://llm-health-guardian-api-1051839420546.us-central1.run.app/`

- OpenAPI docs:  
  `https://llm-health-guardian-api-1051839420546.us-central1.run.app/docs`

Example request:

```bash
curl -X POST \
  "https://llm-health-guardian-api-1051839420546.us-central1.run.app/api/chat" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": \"Explain observability for AI apps like I'm 10.\"}"

Local Development
1. Create and Activate Virtual Environment (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

2. Install Dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

3. Configure Environment Variables

Create a .env file (do not commit this file):

# Google / Vertex
GOOGLE_CLOUD_PROJECT=YOUR_GCP_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL_NAME=gemini-1.5-flash-001

# Datadog
DD_SITE=us5.datadoghq.com
DD_ENV=local
DD_SERVICE=llm-health-guardian-api
DATADOG_API_KEY=YOUR_DATADOG_API_KEY

4. Run the API
uvicorn backend.app.main:app --reload


Open in browser:

http://127.0.0.1:8000/docs

Deploy to Google Cloud Run
1. Authenticate and Set Project
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud config set run/region us-central1

2. Deploy from Source
gcloud run deploy llm-health-guardian-api `
  --source . `
  --region us-central1 `
  --platform managed `
  --allow-unauthenticated

3. Configure Datadog Environment Variables
gcloud run services update llm-health-guardian-api `
  --region us-central1 `
  --update-env-vars DATADOG_API_KEY=YOUR_KEY,DD_SITE=us5.datadoghq.com,DD_ENV=prod,DD_SERVICE=llm-health-guardian-api

Datadog Setup Notes

This project uses:

Datadog Logs Intake API v2

Datadog Metrics API v2 (Series)

Configured monitors detect:

Error-rate spikes

p95 latency regressions

Token anomalies

Cost anomalies

Safety blocks

At least one monitor creates an actionable incident automatically.

Security Considerations

Never commit .env files

Never commit API keys

Use Cloud Run environment variables for production secrets

Scope and rotate Datadog API keys as needed

Repository Structure
backend/
  app/
    main.py
    config.py
    routes/
    telemetry/
Dockerfile
requirements.txt
README.md
LICENSE
.gitignore

Hackathon Context

Built for the Datadog Challenge – Google Cloud Partnerships Hackathon:

LLM application powered by Vertex AI / Gemini

Full telemetry streamed into Datadog

Detection rules and dashboards

Actionable incident response

License

MIT License.
See LICENSE for details.