"""
Locust load test suite for the HAQQ News Verification backend (AWS ECS Fargate).

Focuses on the 3 CREDIT-FREE local endpoints (0 external API tokens used):

  1. HealthCheckUser  — Baseline canary (GET /health)
  2. ClassifyUser     — SetFit ML inference throughput (POST /classify)
  3. OCRUser          — EasyOCR CPU-heavy processing (POST /ocr)
  4. MixedTrafficUser — Combined realistic traffic pattern across these 3 endpoints

Usage:
    # Web UI (interactive):
    locust -f locustfile.py

    # Headless (CI/CD):
    locust -f locustfile.py --headless -u 30 -r 5 --run-time 5m --csv results

    # Single user class only:
    locust -f locustfile.py ClassifyUser
"""

import logging
from collections import defaultdict

from locust import HttpUser, task, between, events, tag

from payloads import (
    get_random_classify_payload,
    get_random_ocr_payload,
)
from config import TARGET_HOST, SLA_MS, VALID_CLASSIFY_LABELS

logger = logging.getLogger("haqq_loadtest")


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Metrics Collection
# ═══════════════════════════════════════════════════════════════════════════════

_custom_metrics = defaultdict(lambda: {
    "sla_breaches": 0,
    "validation_failures": 0,
    "verdict_distribution": defaultdict(int),
    "total_requests": 0,
})


def _record_sla_breach(endpoint: str) -> None:
    _custom_metrics[endpoint]["sla_breaches"] += 1


def _record_validation_failure(endpoint: str) -> None:
    _custom_metrics[endpoint]["validation_failures"] += 1


def _record_verdict(endpoint: str, verdict: str) -> None:
    _custom_metrics[endpoint]["verdict_distribution"][verdict] += 1


def _record_request(endpoint: str) -> None:
    _custom_metrics[endpoint]["total_requests"] += 1


# ═══════════════════════════════════════════════════════════════════════════════
# Response Validation Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _check_sla(endpoint: str, response_time_ms: float) -> bool:
    """Returns True if within SLA, False if breached."""
    threshold = SLA_MS.get(endpoint, 10_000)
    if response_time_ms > threshold:
        _record_sla_breach(endpoint)
        return False
    return True


def _validate_classify_response(response, endpoint: str) -> bool:
    """Validates /classify response schema and values."""
    try:
        data = response.json()
        required_fields = {"label", "score", "news_score", "non_news_score", "is_news"}
        if not required_fields.issubset(data.keys()):
            missing = required_fields - data.keys()
            logger.warning(f"[{endpoint}] Missing fields: {missing}")
            _record_validation_failure(endpoint)
            return False

        if data["label"] not in VALID_CLASSIFY_LABELS:
            logger.warning(f"[{endpoint}] Invalid label: {data['label']}")
            _record_validation_failure(endpoint)
            return False

        if not (0.0 <= data["score"] <= 1.0):
            logger.warning(f"[{endpoint}] Score out of range: {data['score']}")
            _record_validation_failure(endpoint)
            return False

        _record_verdict(endpoint, data["label"])
        return True
    except Exception as e:
        logger.warning(f"[{endpoint}] Validation error: {e}")
        _record_validation_failure(endpoint)
        return False


def _validate_ocr_response(response, endpoint: str) -> bool:
    """Validates /ocr response schema."""
    try:
        data = response.json()
        if "text" not in data:
            logger.warning(f"[{endpoint}] Missing 'text' field in OCR response")
            _record_validation_failure(endpoint)
            return False
        return True
    except Exception as e:
        logger.warning(f"[{endpoint}] Validation error: {e}")
        _record_validation_failure(endpoint)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Event Hooks — Summary at test end
# ═══════════════════════════════════════════════════════════════════════════════

@events.quitting.add_listener
def _print_custom_metrics(environment, **kwargs):
    """Print the custom HAQQ-specific metrics summary when the test ends."""
    print("\n" + "=" * 80)
    print("HAQQ LOAD TEST — CUSTOM METRICS SUMMARY (0 EXTERNAL API CREDITS USED)")
    print("=" * 80)

    for endpoint, metrics in sorted(_custom_metrics.items()):
        total = metrics["total_requests"]
        if total == 0:
            continue

        sla_pct = (metrics["sla_breaches"] / total) * 100
        val_pct = (metrics["validation_failures"] / total) * 100

        print(f"\n  {endpoint}")
        print(f"    Total Requests:        {total}")
        print(f"    SLA Breaches:          {metrics['sla_breaches']} ({sla_pct:.1f}%)")
        print(f"    Validation Failures:   {metrics['validation_failures']} ({val_pct:.1f}%)")

        verdicts = metrics["verdict_distribution"]
        if verdicts:
            print(f"    Verdict Distribution:")
            for verdict, count in sorted(verdicts.items(), key=lambda x: -x[1]):
                pct = (count / total) * 100
                print(f"      {verdict:<20} {count:>5} ({pct:.1f}%)")

    print("\n" + "=" * 80)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HealthCheckUser — Baseline Canary
# ═══════════════════════════════════════════════════════════════════════════════

class HealthCheckUser(HttpUser):
    host = TARGET_HOST
    weight = 1
    wait_time = between(1, 3)

    @task
    @tag("health", "baseline")
    def health_check(self):
        endpoint = "/health"
        _record_request(endpoint)

        with self.client.get(
            endpoint,
            name=endpoint,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Health check failed: HTTP {response.status_code}")
                return

            try:
                data = response.json()
                if data.get("status") != "healthy":
                    response.failure(f"Unexpected health status: {data}")
                    return
            except Exception as e:
                response.failure(f"Invalid JSON response: {e}")
                return

            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ClassifyUser — ML Inference Throughput (FREE / 0 CREDITS)
# ═══════════════════════════════════════════════════════════════════════════════

class ClassifyUser(HttpUser):
    """
    Measures SetFit model inference throughput under concurrent load.
    100% FREE — uses local ML model running on ECS Fargate CPU.
    Great for spiking CPU to test AWS ECS Auto Scaling!
    """
    host = TARGET_HOST
    weight = 5
    wait_time = between(0.5, 2)

    @task
    @tag("classify", "ml-inference")
    def classify_text(self):
        endpoint = "/classify"
        _record_request(endpoint)
        payload = get_random_classify_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=endpoint,
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200] if response.text else 'no body'}"
                )
                return

            if not _validate_classify_response(response, endpoint):
                response.failure("Response validation failed")
                return

            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. OCRUser — CPU-Heavy Image Processing (FREE / 0 CREDITS)
# ═══════════════════════════════════════════════════════════════════════════════

class OCRUser(HttpUser):
    """
    Stresses the EasyOCR endpoint, which is CPU-bound and runs synchronously.
    100% FREE — uses local EasyOCR model running on ECS Fargate CPU.
    """
    host = TARGET_HOST
    weight = 2
    wait_time = between(2, 6)

    @task
    @tag("ocr", "cpu-heavy")
    def ocr_image(self):
        endpoint = "/ocr"
        _record_request(endpoint)
        payload = get_random_ocr_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=endpoint,
            catch_response=True,
            timeout=60,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200] if response.text else 'no body'}"
                )
                return

            if not _validate_ocr_response(response, endpoint):
                response.failure("OCR response validation failed")
                return

            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MixedTrafficUser — Combined Traffic (FREE / 0 CREDITS)
# ═══════════════════════════════════════════════════════════════════════════════

class MixedTrafficUser(HttpUser):
    """
    Simulates realistic extension usage across all 3 credit-free endpoints:
    - 70% /classify (Text classification)
    - 20% /ocr (Image text extraction)
    - 10% /health (Health check)

    Zero external API credits used!
    """
    host = TARGET_HOST
    weight = 8
    wait_time = between(1, 4)

    @task(7)
    @tag("mixed", "classify")
    def classify_flow(self):
        endpoint = "/classify"
        _record_request(endpoint)
        payload = get_random_classify_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=f"{endpoint} [mixed]",
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Classify failed: HTTP {response.status_code}")
                return

            _validate_classify_response(response, endpoint)
            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()

    @task(2)
    @tag("mixed", "ocr")
    def ocr_flow(self):
        endpoint = "/ocr"
        _record_request(endpoint)
        payload = get_random_ocr_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=f"{endpoint} [mixed]",
            catch_response=True,
            timeout=60,
        ) as response:
            if response.status_code != 200:
                response.failure(f"OCR failed: HTTP {response.status_code}")
                return

            _validate_ocr_response(response, endpoint)
            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()

    @task(1)
    @tag("mixed", "health")
    def health_flow(self):
        endpoint = "/health"
        _record_request(endpoint)

        with self.client.get(
            endpoint,
            name=f"{endpoint} [mixed]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
                response.success()
            else:
                response.failure(f"Health check failed: HTTP {response.status_code}")
