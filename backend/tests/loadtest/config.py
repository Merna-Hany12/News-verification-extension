"""
Load test configuration for the HAQQ backend (AWS ECS Fargate).

All values can be overridden via environment variables prefixed with LOCUST_.
"""

import os

# ─── Target Host ──────────────────────────────────────────────────────────────
# Set via env var LOCUST_HOST or --host CLI flag. This is just the fallback.
TARGET_HOST = os.environ.get("LOCUST_HOST", "http://localhost:8000")


# ─── Per-Endpoint P95 Latency SLAs (milliseconds) ────────────────────────────
# Used by the test to flag responses that exceed acceptable thresholds.
# These are *warnings*, not hard failures — they show up in the custom
# metrics summary so you can see how many requests breached SLA.
SLA_MS = {
    "/health":          200,       # Should be near-instant
    "/classify":        500,       # SetFit inference on CPU
    "/ocr":             8_000,     # EasyOCR is CPU-heavy
    "/verify-content":  35_000,    # Full LangGraph pipeline with external APIs
    "/detect-media":    90_000,    # Playwright + GenD + SigLIP
}


# ─── ECS Container Spec (for reference / reporting) ──────────────────────────
ECS_CPU = 2048          # 2 vCPU (Fargate units)
ECS_MEMORY_MB = 10240   # 10 GB
ECS_CONTAINER_PORT = 8000


# ─── Rate Limit Protection ───────────────────────────────────────────────────
# Maximum requests-per-second PER endpoint to avoid burning external API quotas
# during load testing. Set to 0 to disable throttling (full stress test mode).
# These are enforced by a custom wait_time wrapper in locustfile.py.
MAX_RPS = {
    "/health":          0,      # No limit — it's free
    "/classify":        0,      # No external calls
    "/ocr":             0,      # No external calls (just CPU)
    "/verify-content":  2,      # Groq + 3 news APIs have per-minute limits
    "/detect-media":    1,      # Playwright + heavy compute
}


# ─── Stress Test Profiles ───────────────────────────────────────────────────
# Pre-defined user count / spawn rate combinations for common test scenarios.
PROFILES = {
    "smoke": {
        "users": 3,
        "spawn_rate": 1,
        "run_time": "2m",
        "description": "Quick validation that all endpoints respond correctly",
    },
    "baseline": {
        "users": 10,
        "spawn_rate": 2,
        "run_time": "5m",
        "description": "Establish baseline latency under light load",
    },
    "moderate": {
        "users": 30,
        "spawn_rate": 5,
        "run_time": "10m",
        "description": "Moderate load — find the CPU saturation point",
    },
    "stress": {
        "users": 50,
        "spawn_rate": 10,
        "run_time": "15m",
        "description": "Stress test — find the breaking point",
    },
    "spike": {
        "users": 100,
        "spawn_rate": 50,
        "run_time": "5m",
        "description": "Spike test — sudden burst of traffic",
    },
}


# ─── Verdict Values (for response validation) ────────────────────────────────
VALID_VERIFY_VERDICTS = {"fact", "fake", "unverified", "non_news"}
VALID_TEXT_SOURCES = {"direct", "ocr", "ocr_retry", "none"}
VALID_MEDIA_VERDICTS = {"real", "manipulated", "ai_generated", "inconclusive"}
VALID_CLASSIFY_LABELS = {"news", "historical_scientific", "medical", "non_news"}
