# HAQQ Load Testing with Locust (Credit-Free Mode)

Load test suite for the HAQQ News Verification backend deployed on AWS ECS Fargate.

> [!NOTE]
> This suite tests the **3 Credit-Free Endpoints** (`/health`, `/classify`, `/ocr`).
> All models (SetFit & EasyOCR) run **locally inside your ECS container on CPU**.
> **Zero external API credits/tokens (Groq, NewsData, GNews) are consumed!**

## Quick Start

```bash
# 1. Install dependencies
cd backend/tests/loadtest
pip install -r requirements.txt

# 2. Run with Web UI (interactive)
locust -f locustfile.py
# Open http://localhost:8089 in your browser

# 3. Run headless (CI/CD)
locust -f locustfile.py --headless \
    -u 30 -r 5 --run-time 5m \
    --csv results
```

## Test Scenarios

### Individual User Classes

Run a single scenario by specifying the class name:

```bash
# Baseline networking test (GET /health)
locust -f locustfile.py HealthCheckUser

# ML inference throughput (POST /classify) - Best for CPU stress!
locust -f locustfile.py ClassifyUser

# CPU-heavy OCR (POST /ocr)
locust -f locustfile.py OCRUser

# Realistic mixed traffic across all 3 endpoints (RECOMMENDED)
locust -f locustfile.py MixedTrafficUser
```

### Pre-defined Profiles

| Profile | Users | Spawn Rate | Duration | Purpose |
|---------|-------|------------|----------|---------|
| **Smoke** | 3 | 1/s | 2 min | Quick validation that all 3 endpoints respond |
| **Baseline** | 10 | 2/s | 5 min | Establish baseline latency |
| **Moderate** | 30 | 5/s | 10 min | Test CPU saturation point |
| **Stress** | 60 | 10/s | 15 min | Trigger AWS ECS Auto Scaling |
| **Spike** | 100 | 50/s | 5 min | Sudden traffic burst |

Example command for Auto Scaling Stress Test:
```bash
locust -f locustfile.py --headless -u 50 -r 10 --run-time 5m --csv stress_results
```

## What Each Endpoint Tests

### 1. `GET /health` (`HealthCheckUser`)
- **What**: Container liveness, networking/ALB overhead
- **Expected**: <200ms P99, 100% success rate
- **Credits**: 0 (Free)

### 2. `POST /classify` (`ClassifyUser`)
- **What**: SetFit (Transformer) ML model inference throughput on CPU
- **Expected**: <500ms P95
- **Credits**: 0 (Free local SetFit model)
- **Use case**: Perfect for spiking CPU to test AWS ECS Auto Scaling scale-out policies!

### 3. `POST /ocr` (`OCRUser`)
- **What**: EasyOCR text recognition processing on CPU
- **Expected**: <8s P95
- **Credits**: 0 (Free local EasyOCR model)

### 4. `MixedTrafficUser`
- **What**: Realistic mix of text classification (70%), OCR (20%), and health checks (10%)
- **Credits**: 0 (Free)

## Interpreting Results

### Custom HAQQ Summary

At test end, a custom summary prints to stdout:

```
════════════════════════════════════════════════════════════════════════════════
HAQQ LOAD TEST — CUSTOM METRICS SUMMARY (0 EXTERNAL API CREDITS USED)
════════════════════════════════════════════════════════════════════════════════

  /classify
    Total Requests:        450
    SLA Breaches:          12 (2.7%)
    Validation Failures:   0 (0.0%)
    Verdict Distribution:
      news                    240 (53.3%)
      non_news                120 (26.7%)
      medical                 90 (20.0%)
```

## Correlating with AWS CloudWatch

During a load test, open your AWS Console:
1. **AWS ECS Console** → **Clusters** → **Your Cluster** → **Services** → **Metrics tab**
2. Observe **CPU Utilization** spike above 75-90%.
3. Check **Events tab** in ECS to confirm Auto Scaling triggered:
   > *"service default-haqq-backend: has initiated a scale-out activity..."*
