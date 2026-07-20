<p align="center">
  <img src="extension/icons/icon-128.png" alt="HAQQ Logo" width="100" />
</p>

<h1 align="center">HAQQ — حقّق</h1>

<p align="center">
  <strong>AI-Powered Misinformation & Deepfake Detector for Facebook</strong><br/>
  <em>كاشف المحتوى المضلل والوسائط المُولَّدة بالذكاء الاصطناعي على فيسبوك</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Version-4.0-blueviolet?style=for-the-badge" alt="v4.0" />
  <img src="https://img.shields.io/badge/Chrome-Manifest%20V3-blue?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Chrome MV3" />
  <img src="https://img.shields.io/badge/Python-3.11+-yellow?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Docker-Hub-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Hub" />
  <img src="https://img.shields.io/badge/AWS_ECS-Deployed-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white" alt="AWS ECS" />
  <img src="https://img.shields.io/badge/CI%2FCD-GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white" alt="CI/CD" />
  <img src="https://img.shields.io/badge/LangGraph-Pipeline-purple?style=for-the-badge" alt="LangGraph" />
  <img src="https://img.shields.io/badge/Groq-LLaMA_3.3_70B-orange?style=for-the-badge" alt="Groq LLM" />
  <img src="https://img.shields.io/badge/Language-AR%20%2B%20EN-red?style=for-the-badge" alt="Bilingual" />
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-docker--cloud-deployment-aws-ecs">Docker & ECS</a> •
  <a href="#-cicd-pipeline-github-actions">CI/CD</a> •
  <a href="#-features">Features</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-api-reference">API Reference</a> •
  <a href="#-tech-stack">Tech Stack</a>
</p>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [What's New in v4](#-whats-new-in-v4)
- [Demo](#-demo)
- [Features](#-features)
- [Architecture](#-architecture)
- [Verification Pipeline](#-verification-pipeline)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
  - [Backend](#1-backend)
  - [Chrome Extension](#2-chrome-extension)
- [Docker & Cloud Deployment (AWS ECS)](#-docker--cloud-deployment-aws-ecs)
- [CI/CD Pipeline (GitHub Actions)](#-cicd-pipeline-github-actions)
- [Configuration](#%EF%B8%8F-configuration)
- [API Reference](#-api-reference)
- [How It Works](#-how-it-works)
- [Trusted Sources](#-trusted-sources)
- [Benchmarking](#-benchmarking)
- [Tech Stack](#-tech-stack)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🌟 Overview

**HAQQ** (حقّق — Arabic for "verify") is a Chrome extension that combats misinformation on Facebook in real time. It seamlessly integrates into the Facebook feed, providing users with two powerful verification capabilities via a single toolbar on every post:

- **🔍 Content Verification** — Fact-checks text and images using NLP classification, multi-source news retrieval, and LLM-powered reasoning
- **🤖 AI Media Detection** — Identifies AI-generated or manipulated images and videos using a CLIP-based deep learning model

The system produces clear, color-coded verdicts — ✅ **Fact**, ⚠️ **Unverified**, ❌ **Fake**, 🤖 **AI-Generated**, 🛠️ **Manipulated**, or 💬 **Non-News** — all with confidence scores, Arabic explanations, and linked source articles.

---

## 🆕 What's New in v4

<table>
<tr>
<td width="50%">

### 🔀 Merged Content Button
The old three-button layout (📝 نص | 🖼️ صورة | 🎬 فيديو) is replaced with a streamlined **two-button** design:
- **🔍 تحقّق من المحتوى** — Unified text + image verification
- **🤖 كشف وسائط AI** — AI-generated media detection for both images & video

</td>
<td width="50%">

### ⚡ Parallel OCR Pipeline
OCR extraction now fires **in parallel** with text analysis — not sequentially. If the post caption alone is insufficient or comes back `unverified`, the OCR result is already in hand for an instant retry, cutting latency significantly.

</td>
</tr>
<tr>
<td>

### 🎯 GPU-Accelerated Detection
New `/detect-media` endpoint powered by the **GenD** model (CLIP ViT-L/14 backbone with linear probe) for binary classification of real vs. AI-generated content.

</td>
<td>

### 📊 Enhanced Stats Dashboard
The popup dashboard now tracks all verdict categories including `ai_generated`, `manipulated`, `real`, and `inconclusive` alongside the original `fact`/`unverified`/`fake` counters.

</td>
</tr>
</table>

---

## 🎬 Demo

> The repository includes a sample AI-generated video (`AI-generated_video.mp4`) for testing the media detection pipeline.

<details>
<summary><strong>🖥️ How the toolbar looks on Facebook</strong></summary>

When you visit Facebook with HAQQ installed, every post gets a verification toolbar injected below it:

```
┌──────────────────────────────────────────────┐
│  [Facebook Post Content]                      │
│                                               │
│  ┌──────────────────────────────────────────┐ │
│  │ 🔍 حقّق                                  │ │
│  │  [🔍 تحقّق من المحتوى] [🤖 كشف وسائط AI] │ │
│  └──────────────────────────────────────────┘ │
│                                               │
│  ┌──────────────────────────────────────────┐ │
│  │ ✅ المحتوى — موثوق              87% ✕   │ │
│  │ عدة مصادر موثوقة تؤكد هذا الخبر...       │ │
│  │ 📎 المصادر                                │ │
│  │   BBC Arabic · Reuters · الجزيرة          │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

</details>

---

## ✨ Features

### Content Verification

| Feature | Description |
|---------|-------------|
| **Merged Text + Image Flow** | Post text and OCR-extracted image text are processed **in parallel**; the stronger signal is selected for verification. |
| **Three-Class Classification** | Distinguishes between **news**, **historical/scientific content**, and **non-news** (opinions, jokes, memes) using `mDeBERTa-v3-base-mnli-xnli`. |
| **Multi-Source News Search** | Queries NewsData.io, Currents API, GNews, Google News RSS, and DuckDuckGo HTML concurrently. |
| **LLM Cross-Referencing** | Uses Groq (LLaMA 3.3 70B) to compare claims against retrieved articles with structured 3-line verdicts. |
| **Topic Mismatch Detection** | Prevents false confirmations by detecting when articles are topically related but don't actually confirm the specific claim. |
| **Personal Opinion Filter** | Detects first-person opinion markers in Arabic and English to skip verification for subjective posts. |
| **Bilingual Support** | Full Arabic (AR) and English (EN) support — including Arabic-aware keyword extraction, text normalization, and UI. |
| **Image OCR** | Extracts text from images using EasyOCR (Arabic + English), then verifies the extracted content through the full pipeline. |

### AI Media Detection

| Feature | Description |
|---------|-------------|
| **AI-Generated Image Detection** | CLIP-based linear probe model (`GenD`) classifies images as real vs. AI-generated. |
| **Video Analysis** | Analyzes video poster frames and metadata through the same detection pipeline. |
| **Unified Detection Button** | Single 🤖 button handles both image and video detection — backend decides how to weight evidence when both are present. |

### System & Performance

| Feature | Description |
|---------|-------------|
| **Result Caching & Dedup** | Caches verification results and deduplicates in-flight requests to avoid redundant API calls. |
| **API Key Rotation** | Rotates across multiple Groq API keys with automatic rate-limit cooldown to maximize throughput. |
| **Trusted Source Scoring** | 60+ curated trusted sources (international, Arabic, Egyptian, fact-checking, scientific) for confidence boosting. |
| **In-Feed Verdict Badges** | Color-coded, dismissible, animated verdict badges directly in the Facebook feed. |
| **Popup Dashboard** | Extension popup shows verification statistics and lets users manage their API keys. |

---

## 🏗 Architecture

### System Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                       Chrome Extension (MV3)                          │
│                                                                       │
│  ┌───────────┐     ┌────────────────┐     ┌────────────────────────┐  │
│  │  Content   │────▶│ Service Worker │────▶│   FastAPI Backend       │  │
│  │  Script    │     │  (Router v11)  │     │   (via Ngrok tunnel)   │  │
│  │  (v4)      │◀────│                │◀────│                        │  │
│  └───────────┘     └────────────────┘     └────────────────────────┘  │
│                                                                       │
│  Responsibilities:  Responsibilities:      Endpoints:                 │
│  • Scans FB DOM     • Cache + Dedup        • /verify  (LangGraph)     │
│  • Injects toolbar  • Stats tracking       • /classify (ZS-NLI)      │
│  • Extracts text,   • Routes messages      • /ocr     (EasyOCR)      │
│    images, video    • API key config       • /detect-media (GenD)     │
│  • Parallel OCR                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

### LangGraph Verification Pipeline

```
┌─────────────────── LangGraph Pipeline ────────────────────────┐
│                                                                │
│  classify ──▶ extract_keywords ──▶ search ──▶ fetch_bodies    │
│     │                                              │           │
│     ├── non_news ──▶ EXIT                          ▼           │
│     │                                        llm_verify        │
│     └── too short ──▶ EXIT                         │           │
│                                                    ▼           │
│                                                  score ──▶ EXIT│
└────────────────────────────────────────────────────────────────┘
```

### v4 Content Flow (Parallel OCR)

```
┌─── Content Button Clicked ───────────────────────────────────┐
│                                                               │
│  ┌─────────────┐         ┌───────────────┐                   │
│  │ Post Caption │         │ Image OCR     │  ← fires         │
│  │ (instant)    │         │ (async /ocr)  │    immediately    │
│  └──────┬──────┘         └───────┬───────┘                   │
│         │                        │                            │
│         ▼                        │ (in background)            │
│   Caption ≥ 15 chars?            │                            │
│     ├── NO ──▶ await OCR ─────┐  │                            │
│     │                         ▼  ▼                            │
│     └── YES ──▶ /verify ──▶ verdict                          │
│                   │                                           │
│                   └── "unverified"? ──▶ await OCR ──▶ retry   │
└───────────────────────────────────────────────────────────────┘
```

---

## 🔍 Verification Pipeline

The core of HAQQ is a **LangGraph** state machine with the following nodes:

### 1. `classify` — Content Classification
- Uses **mDeBERTa-v3-base-mnli-xnli** (multilingual zero-shot classifier)
- Three-class classification: `news`, `historical_scientific`, `non_news`
- Ambiguity guard: scores below 0.45 default to `news` to avoid false negatives
- Short text (< 20 chars) returns `unverified` immediately

### 2. `extract_keywords` — Keyword Extraction
- **YAKE** (default) or hand-rolled heuristic extractor (A/B testable)
- Arabic-aware stopword filtering (reporting verbs, wire-service boilerplate)
- Inverted-pyramid convention: only the first ~700 characters are analyzed
- Outputs separate `api_query` (95 chars) and `search_query` (200 chars)

### 3. `search` — Multi-Source News Retrieval
- **News path**: NewsData.io + Currents API + GNews + Google RSS in parallel; DuckDuckGo as fallback
- **Historical/Scientific path**: DuckDuckGo + Google RSS (encyclopedic sources rank higher)
- All fetchers run concurrently via `asyncio.gather`
- DuckDuckGo has a 4-second timeout budget

### 4. `fetch_bodies` — Article Body Fetching
- Ranks articles by keyword overlap before fetching
- Downloads top-N article pages (first 30 KB) for real body text
- Strips HTML noise (scripts, nav, footers) and extracts clean paragraphs
- Graceful fallback to snippets on failure (paywall, timeout, 403)

### 5. `llm_verify` — LLM Cross-Referencing
- Sends the claim + top 6 ranked article snippets to **Groq (LLaMA 3.3 70B)**
- Structured 3-line response: `CONFIRMED/UNCONFIRMED/CONTRADICTED`, reasoning, `TOPIC_MATCH/TOPIC_MISMATCH`
- Personal opinion detection bypasses LLM entirely
- Topic mismatch downgrades `CONFIRMED` → `UNCONFIRMED`

### 6. `score` — Final Verdict Scoring

| LLM Verdict | Trusted Sources | Final Verdict | Confidence Range |
|-------------|-----------------|---------------|------------------|
| CONFIRMED | ≥ 1 trusted | ✅ `fact` | 0.80 – 0.97 |
| CONFIRMED | 0 trusted | ✅ `fact` | 0.65 – 0.85 |
| CONTRADICTED | any | ❌ `fake` | 0.70 – 0.92 |
| UNCONFIRMED | ≥ 2 trusted (on-topic) | ✅ `fact` | 0.65 – 0.82 |
| UNCONFIRMED | ≥ 3 untrusted | ⚠️ `unverified` | 0.40 – 0.60 |
| UNCONFIRMED | otherwise | ⚠️ `unverified` | 0.25 |
| NON_NEWS | — | 💬 `non_news` | 0.0 |

---

## 📁 Project Structure

```
News-verification-extension/
│
├── backend/                          # Python FastAPI backend
│   ├── main.py                       # Entry point (uvicorn server)
│   ├── pyproject.toml                # Project metadata & dependencies (uv)
│   ├── requirements.txt              # pip requirements (fallback)
│   ├── uv.lock                       # Reproducible lockfile
│   ├── .env.example                  # Environment variables template
│   │
│   ├── api/                          # API layer
│   │   ├── app.py                    # FastAPI app, CORS, model loading
│   │   ├── routes.py                 # /classify, /verify, /ocr, /detect-media
│   │   └── schemas.py               # Pydantic request models (TextRequest, VerifyRequest, etc.)
│   │
│   ├── core/                         # Core utilities
│   │   ├── config.py                 # API keys, trusted sources, labels
│   │   ├── state.py                  # HAQQState TypedDict (LangGraph state)
│   │   ├── text_processing.py        # Normalization, keyword extraction, RSS parsing
│   │   └── groq_key_rotator.py       # Multi-key async Groq client with rate-limit rotation
│   │
│   ├── graph/                        # LangGraph pipeline
│   │   └── builder.py                # Graph definition, node wiring, run_verify()
│   │
│   ├── nodes/                        # Pipeline nodes
│   │   ├── classify.py               # Zero-shot 3-class classification
│   │   ├── search.py                 # Keyword extraction + multi-API search
│   │   └── verify.py                 # LLM verification + scoring
│   │
│   ├── search/                       # Search backends
│   │   └── fetchers.py               # NewsData, Currents, GNews, Google RSS, DuckDuckGo, body fetcher
│   │
│   ├── models/                       # ML models
│   │   └── gend.py                   # GenD: CLIP-based AI-generated media detector
│   │
│   └── tests/                        # Tests & benchmarks
│       ├── test_imports.py           # Smoke test for all module imports
│       ├── test_text_processing.py   # Unit tests for normalization & keyword extraction
│       └── benchmarks/
│           ├── benchmark.py          # End-to-end benchmark runner
│           └── benchmark_dataset.csv # Evaluation dataset
│
├── extension/                        # Chrome Extension (Manifest V3)
│   ├── manifest.json                 # Extension manifest v3.0.0 (permissions, scripts)
│   │
│   ├── background/                   # Service worker (v11)
│   │   ├── service_worker.js         # Message router, API calls, cache, stats, dedup
│   │   └── config.js                 # NGROK_URL & API keys (gitignored)
│   │
│   ├── content/                      # Content script (v4 — injected into Facebook)
│   │   ├── content.js                # Post scanning, 2-button toolbar, parallel OCR, verdict display
│   │   └── content.css               # Toolbar, buttons & badge styling (gradient themes)
│   │
│   ├── popup/                        # Extension popup
│   │   ├── popup.html                # Settings UI (API key + stats dashboard)
│   │   └── popup.js                  # Popup logic (save/clear key, load stats)
│   │
│   └── icons/
│       └── icon-128.png              # Extension icon
│
├── .github/
│   └── workflows/
│       └── deploy.yml                # CI/CD: Build → ECR → ECS on push to main
│
├── .aws/
│   └── task-definition.json          # ECS Fargate task definition template
│
├── Dockerfile                        # CPU-optimized multi-stage production image
├── docker-compose.yml                # Local / Codespaces compose config
├── .dockerignore                     # Excludes .git, extension, notebooks from image
│
├── notebooks/
│   └── media_pipeline_eval.ipynb     # Evaluation notebook for media analysis pipeline
│
├── AI-generated_video.mp4            # Sample AI-generated video for testing
└── .gitignore
```

---

## 📦 Prerequisites

- **Python** 3.11+
- **[uv](https://docs.astral.sh/uv/)** (fast Python package manager — recommended) or `pip`
- **Google Chrome** (or any Chromium-based browser)
- **[ngrok](https://ngrok.com/)** (for tunneling the local backend to a public URL)

### API Keys Required

| Service | Purpose | Free Tier | Signup |
|---------|---------|-----------|--------|
| [Groq](https://console.groq.com) | LLM inference (LLaMA 3.3 70B) | Free tier available | [Sign up →](https://console.groq.com) |
| [NewsData.io](https://newsdata.io) | News article search | 200 req/day | [Sign up →](https://newsdata.io/register) |
| [Currents API](https://currentsapi.services) | News article search | 600 req/day | [Sign up →](https://currentsapi.services/en/register) |
| [GNews](https://gnews.io) | News article search | 100 req/day | [Sign up →](https://gnews.io/register) |

---

## 🚀 Quick Start

### 1. Backend

```bash
# Clone the repository
git clone https://github.com/Merna-Hany12/News-verification-extension.git
cd News-verification-extension/backend

# Install dependencies with uv (creates .venv automatically)
uv sync
```

> **💡 Tip:** [uv](https://docs.astral.sh/uv/) reads `pyproject.toml` and `uv.lock` for exact, reproducible dependencies. If you don't have uv installed:
> ```bash
> pip install uv        # or see https://docs.astral.sh/uv/getting-started/installation/
> ```
>
> **Alternative (pip):**
> ```bash
> python -m venv .venv && .venv\Scripts\activate   # Windows
> pip install -r requirements.txt
> ```

#### Configure Environment Variables

```bash
# Copy the example .env file
cp .env.example .env
```

Edit `.env` with your API keys:

```env
# ─── News APIs ────────────────────────────────────────────
NEWSDATA_API_KEY="pub_xxxxxxxxxxxxxxxxxxxx"
CURRENTS_API_KEY="your_currents_key"
GNEWS_API_KEY="your_gnews_key"

# ─── LLM (Groq) — supports up to 3 keys for rotation ────
GROQ_API_KEY_1="gsk_xxxxxxxxxxxx"
GROQ_API_KEY_2="gsk_xxxxxxxxxxxx"       # same key OK if you only have one
GROQ_API_KEY_3="gsk_xxxxxxxxxxxx"
GROQ_MODEL="llama-3.3-70b-versatile"

# ─── Ngrok (your tunnel URL, no trailing slash) ──────────
NGROK_URL="https://your-subdomain.ngrok-free.dev"
```

#### Run the Backend

```bash
uv run python -m backend.main
```

The server will:
1. 📦 Load the **mDeBERTa** zero-shot classifier (~1 GB download on first run)
2. 🔤 Initialize **EasyOCR** reader (Arabic + English)
3. 🔗 Compile the **LangGraph** pipeline
4. 🌐 Start listening on `http://0.0.0.0:8000`

#### Expose via ngrok

```bash
ngrok http 8000
```

Copy the generated `https://xxxx.ngrok-free.dev` URL — you'll need it for the extension config.

---

### 2. Chrome Extension

1. Open Chrome → navigate to `chrome://extensions/`
2. Enable **Developer mode** (toggle in the top-right corner)
3. Click **"Load unpacked"**
4. Select the `extension/` folder from this repository

#### Configure the Extension

Create or edit `extension/background/config.js`:

```javascript
export const CONFIG = {
  NEWSDATA_API_KEY: "",        // Optional (used by popup only)
  FREENEWS_API_KEY: "",        // Optional
  NGROK_URL:        "https://your-subdomain.ngrok-free.dev",  // ← Required
};
```

5. **Reload the extension** after updating the config
6. Navigate to **Facebook** — the HAQQ toolbar will appear on every post! 🎉

---

## 🐳 Docker & Cloud Deployment (AWS ECS)

HAQQ is fully containerized and optimized for CPU-only serverless cloud deployments like **AWS ECS (Express Mode)** and **GitHub Codespaces**.

### 1. Local / Codespaces Container Running

To build and run the backend locally or inside a GitHub Codespace using Docker Compose:

```bash
# Start the container
docker compose up -d --build

# View logs to verify startup
docker compose logs -f haqq-backend
```

- **Port Forwarding**: By default, the API will be exposed on `http://localhost:8000`. 
- **In Codespaces**: Go to the **Ports** tab, locate port `8000`, right-click its visibility, and change it from **Private** to **Public** to get an external HTTPS URL for the extension.

---

### 2. Deploying to AWS ECS (Express Mode)

Since HAQQ has a pre-built public Docker image, you can deploy it serverless on AWS without managing virtual machines.

#### Step 2.1: Push Your Docker Image to Amazon ECR
1. Navigate to the **Amazon ECR** console.
2. Click **Create repository** → set visibility to **Public** → Name it `haqq-backend-cpu`.
3. In your local terminal, log in to ECR, tag, and push your image:
   ```bash
   # Log in to Public ECR
   aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws

   # Tag your image (replace YOUR_ECR_URI with your repository's public URI)
   docker tag darcklord/haqq-backend-cpu:latest YOUR_ECR_URI:latest

   # Push the image
   docker push YOUR_ECR_URI:latest
   ```

#### Step 2.2: Launch on ECS Express Mode
1. Open the **Amazon ECS** console.
2. Click **Create service** and choose **Express Mode**.
3. Configure the container:
   - **Image URI**: Enter your ECR repository URI.
   - **Service Name**: `haqq-backend`
   - **Container Port**: `8000`
4. Set Resource allocations:
   - **CPU**: `2 vCPU`
   - **Memory**: `8 GB`
5. Input your Environment Variables under container configuration (copy values from your local `.env` file):
   - `NEWSDATA_API_KEY`, `CURRENTS_API_KEY`, `GNEWS_API_KEY`, `GROQ_API_KEY`, `GROQ_MODEL`.
6. Click **Create**. ECS Express Mode will automatically spin up the networking, security groups, and an Application Load Balancer (ALB) with an HTTPS URL.

#### Step 2.3: Point Your Extension to ECS
1. Copy the DNS name of the Load Balancer generated by ECS (e.g. `https://xxxx.elb.amazonaws.com`).
2. Update the `NGROK_URL` key in `extension/background/config.js` with this URL.
3. Update `host_permissions` in `extension/manifest.json` to include `"https://*.elb.amazonaws.com/*"`.
4. Reload the extension in Chrome.

---

## 🔄 CI/CD Pipeline (GitHub Actions)

HAQQ includes a fully automated CI/CD pipeline that builds, pushes, and deploys the backend whenever code changes are pushed to `main`.

```
  git push origin main (backend/ changed)
        │
        ▼
  GitHub Actions triggers automatically
        │
        ├── 1. Build Docker image
        ├── 2. Push to Amazon ECR (tagged: sha-abc123 + latest)
        ├── 3. Update ECS task definition with new image
        ├── 4. Deploy new task to ECS service
        └── 5. Wait for deployment to stabilize ✅
```

### Key Features

| Feature | Description |
|---|---|
| **Path filtering** | Only triggers when `backend/`, `Dockerfile`, or `requirements.deploy.txt` change |
| **OIDC authentication** | Uses OpenID Connect to assume an AWS IAM Role — no long-lived access keys |
| **Dual tagging** | Each image is tagged with `latest` + git commit SHA for traceability and rollback |
| **Concurrency control** | Cancels in-progress deployments if a new push arrives |
| **Health check** | Waits for ECS service to stabilize before marking success |
| **Manual trigger** | Can also be triggered manually from the GitHub Actions tab |

### Setup Requirements

Before the pipeline works, you need to configure the following **one-time** AWS and GitHub settings:

1. **AWS**: Create an ECR private repository named `haqq-backend`
2. **AWS**: Add GitHub as an OIDC Identity Provider in IAM
3. **AWS**: Create an IAM Role (`github-actions-haqq-deploy`) trusted by the GitHub OIDC provider
4. **GitHub**: Add these repository secrets under **Settings → Secrets → Actions**:

| Secret | Description |
|---|---|
| `AWS_ROLE_ARN` | ARN of the IAM role for GitHub Actions |
| `AWS_REGION` | AWS region (e.g., `eu-central-1`) |
| `ECR_REPOSITORY` | ECR repository name (`haqq-backend`) |
| `ECS_CLUSTER` | Name of your ECS cluster |
| `ECS_SERVICE` | Name of your ECS service |

### Files

| File | Purpose |
|---|---|
| `.github/workflows/deploy.yml` | GitHub Actions workflow definition |
| `.aws/task-definition.json` | ECS Fargate task definition template (image placeholder is replaced on each deploy) |

---

## ⚙️ Configuration

### Backend (`backend/core/config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model for LLM verification |
| `BODY_CHARS_PER_ARTICLE` | `500` | Max characters of article body sent to LLM |
| `BODY_FETCH_TOP_N` | `2` | Number of articles to fetch full body for |
| `API_QUERY_LIMIT` | `95` | Max query length for paid news APIs |
| `SEARCH_QUERY_LIMIT` | `200` | Max query length for search backends |

### Keyword Extraction (`backend/core/text_processing.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `KEYWORD_EXTRACTOR_METHOD` | `"yake"` | `"yake"` or `"heuristic"` |
| `LEAD_CHARS` | `700` | Characters from start of text to analyze |
| `YAKE_TOP` | `20` | Max keywords returned by YAKE |
| `YAKE_NGRAM` | `3` | Max n-gram size for keyword phrases |

### Content Script (`extension/content/content.js`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MIN_TEXT_LEN` | `15` | Minimum characters before text is considered enough for verification |
| `DEBUG` | `true` | Enables `[HAQQ]` console logging |

---

## 📡 API Reference

### `POST /classify`

Classify text as news or non-news using zero-shot NLI classification.

<details>
<summary><strong>Request / Response</strong></summary>

**Request:**
```json
{
  "text": "عاجل: زلزال بقوة 6.5 يضرب تركيا"
}
```

**Response:**
```json
{
  "label": "news report breaking news...",
  "score": 0.89,
  "news_score": 0.89,
  "non_news_score": 0.11,
  "is_news": true
}
```

</details>

---

### `POST /verify`

Run the full LangGraph verification pipeline on a claim.

<details>
<summary><strong>Request / Response</strong></summary>

**Request:**
```json
{
  "text": "ناسا تعلن اكتشاف كوكب جديد صالح للحياة",
  "lang": "ar"
}
```

**Response:**
```json
{
  "verdict": "fact",
  "confidence": 0.87,
  "explanation": "عدة مصادر موثوقة تؤكد إعلان ناسا عن اكتشاف كوكب...",
  "sources": [
    {
      "url": "https://www.bbc.com/arabic/...",
      "title": "ناسا تعلن اكتشاف كوكب...",
      "trusted": true,
      "overlap": 4
    }
  ]
}
```

**Possible `verdict` values:** `fact` · `unverified` · `fake` · `non_news`

</details>

---

### `POST /ocr`

Extract text from an image using EasyOCR (Arabic + English).

<details>
<summary><strong>Request / Response</strong></summary>

**Request:**
```json
{
  "image_url": "https://scontent.fcai1-1.fna.fbcdn.net/v/..."
}
```

**Response:**
```json
{
  "text": "الحكومة تعلن عن زيادة جديدة في الرواتب"
}
```

</details>

---

### `POST /detect-media`

Detect AI-generated or manipulated media (images and/or video).

<details>
<summary><strong>Request / Response</strong></summary>

**Request:**
```json
{
  "image_url": "https://scontent.fcai1-1.fna.fbcdn.net/v/...",
  "video_url": null
}
```

**Response:**
```json
{
  "verdict": "ai_generated",
  "confidence": 0.92,
  "explanation": "تم الكشف عن أن هذه الصورة مُولَّدة بالذكاء الاصطناعي بدرجة ثقة عالية.",
  "sources": []
}
```

**Possible `verdict` values:** `real` · `ai_generated` · `manipulated` · `inconclusive`

</details>

---

## 🔧 How It Works

### On Facebook (Extension Side)

```
User scrolls Facebook
        │
        ▼
MutationObserver detects new posts
        │
        ▼
Content script extracts text, images, video URLs
        │
        ▼
HAQQ toolbar is injected (2 buttons)
        │
        ├──▶ [🔍 Content]                    ├──▶ [🤖 AI Media]
        │     • Clean text                    │     • Send image/video URL
        │     • Fire OCR in parallel          │       to /detect-media
        │     • Auto-detect language          │     • GenD model classifies
        │     • Send to /verify               │       real vs AI-generated
        │     • If "unverified" → retry       │
        │       with OCR text                 │
        ▼                                     ▼
Color-coded verdict badge displayed
        │
        ├── ✅ Fact (green)
        ├── ⚠️ Unverified (amber)
        ├── ❌ Fake (red)
        ├── 🤖 AI-Generated (blue)
        ├── 🛠️ Manipulated (orange)
        └── 💬 Non-News (grey)
```

### On the Backend (Python Side)

1. **FastAPI** receives the request and routes it to the appropriate handler
2. **`/verify`** → LangGraph pipeline: classify → extract keywords → search → fetch bodies → LLM verify → score
3. **`/detect-media`** → GenD model (CLIP ViT-L/14 + linear probe): download → preprocess → GPU inference → verdict
4. **`/ocr`** → EasyOCR: download image → extract text (AR + EN) → return
5. Each pipeline node can **early-exit** (e.g., non-news content skips verification entirely)
6. The **Groq key rotator** handles rate limits transparently across multiple API keys
7. Final result is returned as structured JSON with verdict, confidence, explanation, and sources

---

## 🏛 Trusted Sources

HAQQ maintains a curated list of **60+ trusted sources** used for confidence boosting:

| Category | Examples |
|----------|---------|
| **International News** | BBC, Reuters, AP, Al Jazeera, CNN, NYT, The Guardian, France24, DW |
| **Fact-Checking** | Snopes, PolitiFact, FactCheck.org, Full Fact |
| **Scientific** | Wikipedia, Britannica, Nature, PubMed, NASA, WHO, CDC, arXiv |
| **Arabic Media** | الجزيرة, العربية, الحرة, سكاي نيوز عربية, بي بي سي عربي |
| **Egyptian Media** | الأهرام, اليوم السابع, المصري اليوم, الشروق, مصراوي |

Trusted source matching uses **word-boundary regex** to prevent false positives (e.g., "time" won't match "The Economic Times").

---

## 📊 Benchmarking

The project includes a benchmark suite for evaluating the keyword extraction and verification pipeline:

```bash
cd backend
python -m tests.benchmarks.benchmark
```

The benchmark:
- A/B tests YAKE vs heuristic keyword extractors
- Evaluates against a labeled dataset (`benchmark_dataset.csv`)
- Measures precision, recall, and F1 across verdict classes

The evaluation notebook (`notebooks/media_pipeline_eval.ipynb`) provides additional analysis for the media detection pipeline.

---

## 🛠 Tech Stack

### Backend

| Technology | Purpose |
|-----------|---------|
| **[uv](https://docs.astral.sh/uv/)** | Fast Python package manager & virtualenv tool |
| **[FastAPI](https://fastapi.tiangolo.com/)** | Async web framework for the REST API |
| **[LangGraph](https://langchain-ai.github.io/langgraph/)** | State machine framework for the verification pipeline |
| **[Transformers](https://huggingface.co/docs/transformers)** (HuggingFace) | mDeBERTa zero-shot classifier |
| **[Groq](https://console.groq.com/)** | LLM inference (LLaMA 3.3 70B) |
| **[EasyOCR](https://github.com/JaidedAI/EasyOCR)** | Optical character recognition (Arabic + English) |
| **[YAKE](https://github.com/LIAAD/yake)** | Unsupervised keyword extraction |
| **[CLIP](https://github.com/openai/CLIP)** (OpenAI) | Vision backbone for GenD AI-media detector |
| **[PyTorch](https://pytorch.org/)** | Deep learning runtime |
| **[httpx](https://www.python-httpx.org/)** | Async HTTP client for news API fetching |
| **[Pydantic](https://docs.pydantic.dev/)** | Request validation and serialization |
| **[uvicorn](https://www.uvicorn.org/)** | ASGI server |

### Extension

| Technology | Purpose |
|-----------|---------|
| **Chrome Manifest V3** | Extension platform |
| **Service Worker (v11)** | Background message routing, caching & deduplication |
| **Content Script (v4)** | Facebook DOM manipulation, 2-button toolbar & verdict UI |
| **MutationObserver** | Real-time post detection as user scrolls |

### DevOps & Infrastructure

| Technology | Purpose |
|-----------|---------|
| **[Docker](https://www.docker.com/)** | CPU-optimized multi-stage containerized build |
| **[Docker Hub](https://hub.docker.com/r/darcklord/haqq-backend-cpu)** | Public image registry (`darcklord/haqq-backend-cpu`) |
| **[Amazon ECR](https://aws.amazon.com/ecr/)** | Private container registry for ECS deployments |
| **[Amazon ECS Express Mode](https://aws.amazon.com/ecs/)** | Serverless container orchestration (Fargate) |
| **[GitHub Actions](https://github.com/features/actions)** | CI/CD pipeline: auto build → push → deploy on every push to `main` |

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. **Fork** the repository
2. **Create a branch** for your feature: `git checkout -b feature/my-feature`
3. **Commit** your changes: `git commit -m 'Add my feature'`
4. **Push** to the branch: `git push origin feature/my-feature`
5. **Open a Pull Request**

### Areas Where Help is Needed

- 🎯 **Wire the GenD model** into the `/detect-media` endpoint (currently returns a stub)
- 🧪 **Expand the benchmark dataset** with more Arabic-language claims
- 🌍 **Add more trusted sources** for underrepresented regions
- 📱 **Firefox/Edge extension** port
- 🎨 **Dark mode support** for the in-feed badges

---

## 📄 License

This project is open source. See the repository for license details.

---

<p align="center">
  <img src="extension/icons/icon-128.png" alt="HAQQ" width="40" /><br/>
  <strong>HAQQ — حقّق</strong><br/>
  <em>Making Arabic & English fact-checking accessible to everyone.</em><br/><br/>
  <sub>Built with ❤️ for a more truthful internet.</sub>
</p>
