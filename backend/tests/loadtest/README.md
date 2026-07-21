# HAQQ Load Testing with Locust

Load test suite for the HAQQ News Verification backend deployed on AWS ECS Fargate.

## Quick Start

```bash
# 1. Install dependencies
cd backend/tests/loadtest
pip install -r requirements.txt

# 2. Run with Web UI (interactive)
locust -f locustfile.py --host http://<YOUR_ECS_ALB_URL>
# Open http://localhost:8089 in your browser

# 3. Run headless (CI/CD)
locust -f locustfile.py --headless \
    -u 30 -r 5 --run-time 10m \
    --host http://<YOUR_ECS_ALB_URL> \
    --csv results
```

## Test Scenarios

### Individual User Classes

Run a single scenario by specifying the class name:

```bash
# Baseline networking test
locust -f locustfile.py HealthCheckUser --host http://<URL>

# ML inference throughput
locust -f locustfile.py ClassifyUser --host http://<URL>

# CPU-heavy OCR
locust -f locustfile.py OCRUser --host http://<URL>

# Full pipeline stress
locust -f locustfile.py VerifyContentUser --host http://<URL>

# Heavyweight media analysis
locust -f locustfile.py DetectMediaUser --host http://<URL>

# Realistic mixed traffic (RECOMMENDED)
locust -f locustfile.py MixedTrafficUser --host http://<URL>
```

### Pre-defined Profiles

Use these parameter combinations for different test intensities:

| Profile | Users | Spawn Rate | Duration | Purpose |
|---------|-------|------------|----------|---------|
| **Smoke** | 3 | 1/s | 2 min | Validate all endpoints work |
| **Baseline** | 10 | 2/s | 5 min | Establish baseline latency |
| **Moderate** | 30 | 5/s | 10 min | Find CPU saturation point |
| **Stress** | 50 | 10/s | 15 min | Find breaking point |
| **Spike** | 100 | 50/s | 5 min | Sudden traffic burst |

Example:
```bash
# Moderate load test
locust -f locustfile.py --headless -u 30 -r 5 --run-time 10m \
    --host http://<URL> --csv moderate_results
```

## What Each Test Measures

### `/health` (HealthCheckUser)
- **What**: Container liveness, networking/ALB overhead
- **Expected**: <50ms P99, 100% success rate
- **If slow**: ECS task unhealthy, network issues, or container overloaded

### `/classify` (ClassifyUser)
- **What**: SetFit model inference throughput (CPU-bound, synchronous)
- **Expected**: <500ms P95 at 10 concurrent users
- **If slow**: CPU saturation from other endpoints (OCR, GenD)
- **Key metric**: Max requests/sec before latency degrades

### `/ocr` (OCRUser)
- **What**: EasyOCR CPU throughput, event loop blocking
- **Expected**: <8s P95 at 5 concurrent users
- **If slow**: Normal — OCR is inherently CPU-heavy
- **Watch for**: Other endpoints (/health, /classify) timing out while OCR runs

### `/verify-content` (VerifyContentUser)
- **What**: Full LangGraph pipeline end-to-end
- **Expected**: <35s P95, <5% error rate
- **If errors spike**: Groq rate limits (check `rate_limit_hits` in summary)
- **If all "unverified"**: News API quotas exhausted
- **Key metric**: Verdict distribution — should NOT be 100% unverified

### `/detect-media` (DetectMediaUser)
- **What**: YuNet + GenD + SigLIP inference, image download
- **Expected**: <90s P95 at 3 concurrent users
- **If OOM**: Too many concurrent requests creating tensors/PIL images
- **Key metric**: Memory usage correlation in CloudWatch

### Mixed Traffic (MixedTrafficUser)
- **What**: Realistic contention between all endpoint types
- **Expected**: classify stays fast even when OCR/detect-media are busy
- **Key insight**: Whether CPU-bound ops (classify, OCR, GenD) starve I/O-bound ops (verify-content external API calls)

## Interpreting Results

### Locust Output Files

When using `--csv results`, Locust generates:
- `results_stats.csv` — Per-endpoint: avg/median/P95/P99 latency, RPS, failure rate
- `results_failures.csv` — Detailed failure messages per endpoint
- `results_stats_history.csv` — Time series of all metrics

### Custom HAQQ Metrics

At test end, a custom summary prints to stdout:

```
════════════════════════════════════════════════════════════════════════════════
HAQQ LOAD TEST — CUSTOM METRICS SUMMARY
════════════════════════════════════════════════════════════════════════════════

  /verify-content
    Total Requests:        150
    SLA Breaches:          12 (8.0%)
    Rate Limit Hits:       5 (3.3%)
    Validation Failures:   0 (0.0%)
    Verdict Distribution:
      fact                    45 (30.0%)
      unverified              60 (40.0%)
      fake                    20 (13.3%)
      non_news                25 (16.7%)
```

### What to Look For

1. **SLA Breaches > 20%**: The container is CPU-saturated; consider scaling horizontally (more ECS tasks) or vertically (more vCPU)
2. **Rate Limit Hits > 10%**: Groq or news API quotas are being hit; reduce test intensity or add more API keys
3. **Validation Failures > 0%**: The server is returning malformed responses under load (potential concurrency bug)
4. **Verdict Distribution all "unverified"**: External APIs are exhausted — this is a cost/quota issue, not a performance issue
5. **Health check failures**: Container is unhealthy or unresponsive — check ECS task status in AWS Console

## Correlating with AWS CloudWatch

During a load test, open these CloudWatch dashboards side-by-side with Locust:

### ECS Task Metrics
- **CPUUtilization**: Should correlate with Locust RPS. If CPU hits ~100% on 2 vCPU, that's the throughput ceiling.
- **MemoryUtilization**: Watch for upward trends without recovery — indicates memory leaks. The 10GB limit is generous but 4 ML models + Playwright can eat it.
- **RunningTaskCount**: If using auto-scaling, watch this increase when CPU threshold is breached.

### ALB Metrics (if behind a load balancer)
- **TargetResponseTime**: Should match Locust's reported latency.
- **RequestCount**: Should match Locust's total RPS.
- **HTTPCode_Target_5XX_Count**: Server errors under load.
- **HealthyHostCount**: Should stay at task count. If it drops, containers are crashing.

### How to Find These
1. AWS Console → ECS → Clusters → Your Cluster → Service → Metrics tab
2. AWS Console → CloudWatch → Metrics → ECS → filter by cluster/service name
3. For ALB: CloudWatch → Metrics → ApplicationELB → TargetGroup

## File Structure

```
backend/tests/loadtest/
├── locustfile.py       # 6 test scenarios with response validation
├── payloads.py         # Realistic Arabic/English test data
├── config.py           # SLA thresholds, rate limits, profiles
├── requirements.txt    # locust>=2.29
└── README.md           # This file
```

## Troubleshooting

### "Connection refused" or "Connection reset"
- The ECS service might not be running. Check `aws ecs describe-services`.
- The ALB security group might not allow inbound traffic on port 8000/443.

### All requests return 500
- The container is still loading models (120s startup). Wait and retry.
- Check ECS task logs: `aws logs get-log-events --log-group-name /aws/ecs/default/haqq-backend-6edb`

### Extremely slow responses (>60s)
- `/verify-content` depends on external APIs; if they're slow, the whole pipeline is slow.
- `/detect-media` with video URLs spawns Playwright — this is expected to be slow.

### "Rate limited" errors
- Groq API has per-minute rate limits. The 3-key rotator helps but has limits.
- NewsData/GNews/Currents have daily quotas that may exhaust during extended tests.
- Reduce user count or increase `wait_time` in the test.
