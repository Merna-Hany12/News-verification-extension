"""
Load test configuration for the HAQQ backend (AWS ECS Fargate).

All values can be overridden via environment variables prefixed with LOCUST_.
"""

import os

# ─── Target Host ──────────────────────────────────────────────────────────────
# Set via env var LOCUST_HOST or --host CLI flag. This is just the fallback.
TARGET_HOST = os.environ.get("LOCUST_HOST", "https://ha-d1503bd4c01449c09f983bd5ec1cc3b3.ecs.us-east-1.on.aws")


# ─── Per-Endpoint P95 Latency SLAs (milliseconds) ────────────────────────────
# Used by the test to flag responses that exceed acceptable thresholds.
# These 3 endpoints run 100% locally in the ECS container (0 external API credits).
SLA_MS = {
    "/health":          200,       # Near-instant heartbeat
    "/classify":        500,       # SetFit ML inference on CPU
    "/ocr":             8_000,     # EasyOCR processing on CPU
}


# ─── ECS Container Spec (for reference / reporting) ──────────────────────────
ECS_CPU = 2048          # 2 vCPU (Fargate units)
ECS_MEMORY_MB = 10240   # 10 GB
ECS_CONTAINER_PORT = 8000


# ─── Rate Limit Protection ───────────────────────────────────────────────────
# No limits needed for these 3 endpoints as they consume zero external API quotas.
MAX_RPS = {
    "/health":          0,      # No limit — 100% free
    "/classify":        0,      # No limit — 100% free (SetFit local ML model)
    "/ocr":             0,      # No limit — 100% free (EasyOCR local model)
}


# ─── Stress Test Profiles ───────────────────────────────────────────────────
# Pre-defined user count / spawn rate combinations for common test scenarios.
PROFILES = {
    "smoke": {
        "users": 3,
        "spawn_rate": 1,
        "run_time": "2m",
        "description": "Quick validation that all 3 local endpoints respond correctly",
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
        "description": "Moderate load — test CPU saturation and Auto Scaling",
    },
    "stress": {
        "users": 60,
        "spawn_rate": 10,
        "run_time": "15m",
        "description": "Stress test — heavy CPU load to trigger ECS scale-out",
    },
    "spike": {
        "users": 100,
        "spawn_rate": 50,
        "run_time": "5m",
        "description": "Spike test — sudden burst of traffic",
    },
}


# ─── Verdict Values (for response validation) ────────────────────────────────
VALID_CLASSIFY_LABELS = {"news", "historical_scientific", "medical", "non_news"}
