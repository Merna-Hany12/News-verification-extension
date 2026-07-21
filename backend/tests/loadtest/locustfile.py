"""
Locust load test suite for the HAQQ News Verification backend (AWS ECS Fargate).

Provides 6 user classes that can be run independently or together:

  1. HealthCheckUser     — Baseline canary (GET /health)
  2. ClassifyUser        — SetFit ML inference throughput (POST /classify)
  3. OCRUser             — EasyOCR CPU-heavy processing (POST /ocr)
  4. VerifyContentUser   — Full LangGraph pipeline stress (POST /verify-content)
  5. DetectMediaUser     — Heavyweight media analysis (POST /detect-media)
  6. MixedTrafficUser    — Realistic combined traffic pattern

Usage:
    # Web UI (interactive):
    locust -f locustfile.py --host http://<ECS_ALB_URL>

    # Headless (CI/CD):
    locust -f locustfile.py --headless -u 30 -r 5 --run-time 10m \
        --host http://<ECS_ALB_URL> --csv results

    # Single user class only:
    locust -f locustfile.py --host http://<ECS_ALB_URL> ClassifyUser
"""

import time
import json
import logging
from collections import defaultdict

from locust import HttpUser, task, between, events, tag

from payloads import (
    get_random_classify_payload,
    get_random_verify_payload,
    get_random_ocr_payload,
    get_random_detect_media_payload,
    get_news_classify_payload,
)
from config import SLA_MS, VALID_VERIFY_VERDICTS, VALID_TEXT_SOURCES, VALID_MEDIA_VERDICTS, VALID_CLASSIFY_LABELS

logger = logging.getLogger("haqq_loadtest")


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Metrics Collection
# ═══════════════════════════════════════════════════════════════════════════════

# Track per-endpoint metrics beyond what Locust provides by default.
_custom_metrics = defaultdict(lambda: {
    "sla_breaches": 0,
    "rate_limit_hits": 0,
    "validation_failures": 0,
    "verdict_distribution": defaultdict(int),
    "total_requests": 0,
})


def _record_sla_breach(endpoint: str) -> None:
    _custom_metrics[endpoint]["sla_breaches"] += 1


def _record_rate_limit(endpoint: str) -> None:
    _custom_metrics[endpoint]["rate_limit_hits"] += 1


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
    threshold = SLA_MS.get(endpoint, 60_000)
    if response_time_ms > threshold:
        _record_sla_breach(endpoint)
        return False
    return True


def _is_rate_limited(response) -> bool:
    """Detects rate limiting from both HTTP status and Groq error messages."""
    if response.status_code == 429:
        return True
    try:
        body = response.json()
        # Groq rate limit errors sometimes come as 200 with error in body
        error_msg = str(body.get("detail", "")).lower()
        if "rate_limit" in error_msg or "429" in error_msg:
            return True
    except Exception:
        pass
    return False


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


def _validate_verify_response(response, endpoint: str) -> bool:
    """Validates /verify-content response schema and values."""
    try:
        data = response.json()
        required_fields = {"verdict", "confidence", "explanation", "sources"}
        if not required_fields.issubset(data.keys()):
            missing = required_fields - data.keys()
            logger.warning(f"[{endpoint}] Missing fields: {missing}")
            _record_validation_failure(endpoint)
            return False

        verdict = data["verdict"]
        if verdict not in VALID_VERIFY_VERDICTS:
            logger.warning(f"[{endpoint}] Invalid verdict: {verdict}")
            _record_validation_failure(endpoint)
            return False

        confidence = data["confidence"]
        # confidence can be 0.0 for non_news/unverified
        if not (0.0 <= confidence <= 1.0):
            logger.warning(f"[{endpoint}] Confidence out of range: {confidence}")
            _record_validation_failure(endpoint)
            return False

        text_source = data.get("text_source", "")
        if text_source and text_source not in VALID_TEXT_SOURCES:
            logger.warning(f"[{endpoint}] Invalid text_source: {text_source}")
            _record_validation_failure(endpoint)
            return False

        _record_verdict(endpoint, verdict)
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


def _validate_detect_media_response(response, endpoint: str) -> bool:
    """Validates /detect-media response schema and values."""
    try:
        data = response.json()
        required_fields = {"verdict", "confidence", "explanation"}
        if not required_fields.issubset(data.keys()):
            missing = required_fields - data.keys()
            logger.warning(f"[{endpoint}] Missing fields: {missing}")
            _record_validation_failure(endpoint)
            return False

        verdict = data["verdict"]
        if verdict not in VALID_MEDIA_VERDICTS:
            logger.warning(f"[{endpoint}] Invalid media verdict: {verdict}")
            _record_validation_failure(endpoint)
            return False

        # Check metadata if present
        metadata = data.get("metadata", {})
        if metadata:
            if "frames" in metadata and not isinstance(metadata["frames"], list):
                logger.warning(f"[{endpoint}] metadata.frames is not a list")
                _record_validation_failure(endpoint)
                return False

        _record_verdict(endpoint, verdict)
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
    print("HAQQ LOAD TEST — CUSTOM METRICS SUMMARY")
    print("=" * 80)

    for endpoint, metrics in sorted(_custom_metrics.items()):
        total = metrics["total_requests"]
        if total == 0:
            continue

        sla_pct = (metrics["sla_breaches"] / total) * 100
        rl_pct = (metrics["rate_limit_hits"] / total) * 100
        val_pct = (metrics["validation_failures"] / total) * 100

        print(f"\n  {endpoint}")
        print(f"    Total Requests:        {total}")
        print(f"    SLA Breaches:          {metrics['sla_breaches']} ({sla_pct:.1f}%)")
        print(f"    Rate Limit Hits:       {metrics['rate_limit_hits']} ({rl_pct:.1f}%)")
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
    """
    Continuously polls GET /health to establish baseline latency and verify
    the container is alive. This is the simplest possible request — any
    failures or latency spikes here indicate infrastructure-level problems
    (ECS task unhealthy, ALB routing issues, network congestion) rather
    than application-level bottlenecks.

    Run alone to establish the networking baseline before adding heavier
    endpoints.
    """
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
# 2. ClassifyUser — ML Inference Throughput
# ═══════════════════════════════════════════════════════════════════════════════

class ClassifyUser(HttpUser):
    """
    Measures SetFit model inference throughput under concurrent load.

    Key insights this test reveals:
    - The CPU-bound throughput ceiling (requests/sec) for a single uvicorn
      worker with 2 vCPU Fargate.
    - Latency degradation curve as concurrent users increase — since SetFit
      is synchronous, requests queue behind each other.
    - Whether the classifier holds up under sustained load without memory
      leaks or model corruption.
    """
    weight = 3
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
            if _is_rate_limited(response):
                _record_rate_limit(endpoint)
                response.failure("Rate limited")
                return

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
# 3. OCRUser — CPU-Heavy Image Processing
# ═══════════════════════════════════════════════════════════════════════════════

class OCRUser(HttpUser):
    """
    Stresses the EasyOCR endpoint, which is CPU-bound and runs synchronously
    (blocking the event loop while processing).

    Key insights:
    - How many concurrent OCR requests before the event loop starves and
      /health or /classify start timing out.
    - Memory spikes from PIL/numpy image array creation per request.
    - Whether EasyOCR's internal state is thread-safe under contention.
    """
    weight = 2
    wait_time = between(3, 8)

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
# 4. VerifyContentUser — Full Pipeline Stress
# ═══════════════════════════════════════════════════════════════════════════════

class VerifyContentUser(HttpUser):
    """
    Exercises the complete LangGraph verification pipeline:
      classify → extract_keywords → search (3 APIs + RSS + DDG fallback)
      → fetch_bodies → llm_verify (Groq) → score

    This is the primary user-facing endpoint and the most complex to test
    because it depends on multiple external services (Groq LLM, NewsData,
    Currents, GNews, Google RSS, DuckDuckGo).

    Key insights:
    - End-to-end latency distribution under concurrent load.
    - Rate limiting from Groq (3-key rotator) and news APIs.
    - Whether the Groq key rotator distributes load evenly.
    - Verdict distribution — if everything comes back as "unverified",
      that suggests API quotas are exhausted.
    - Memory stability during concurrent LangGraph pipeline executions.
    """
    weight = 4
    wait_time = between(5, 15)

    @task
    @tag("verify", "full-pipeline")
    def verify_content(self):
        endpoint = "/verify-content"
        _record_request(endpoint)
        payload = get_random_verify_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=endpoint,
            catch_response=True,
            timeout=120,
        ) as response:
            if _is_rate_limited(response):
                _record_rate_limit(endpoint)
                response.failure("Rate limited (Groq or news API quota)")
                return

            if response.status_code == 500:
                # Server-side errors often indicate model not loaded or
                # pipeline compilation failure
                detail = ""
                try:
                    detail = response.json().get("detail", "")
                except Exception:
                    detail = response.text[:200] if response.text else ""
                response.failure(f"Server error: {detail}")
                return

            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200] if response.text else 'no body'}"
                )
                return

            if not _validate_verify_response(response, endpoint):
                response.failure("Verify response validation failed")
                return

            resp_time_ms = response.elapsed.total_seconds() * 1000
            _check_sla(endpoint, resp_time_ms)

            # Log slow requests for investigation
            if resp_time_ms > 20_000:
                try:
                    data = response.json()
                    logger.info(
                        f"[SLOW] /verify-content {resp_time_ms:.0f}ms — "
                        f"verdict={data.get('verdict')} "
                        f"text_source={data.get('text_source')} "
                        f"text={payload.get('text', '')[:60]}"
                    )
                except Exception:
                    pass

            response.success()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DetectMediaUser — Heavyweight Media Analysis
# ═══════════════════════════════════════════════════════════════════════════════

class DetectMediaUser(HttpUser):
    """
    Stresses the most resource-intensive endpoint:
    - Downloads image from URL
    - YuNet face detection on each frame
    - GenD deepfake inference (if faces found)
    - SigLIP AIGC classification on all frames
    - Face-aware fusion scoring

    Uses only image_url payloads (single-image path) because video URLs
    require accessible CDN links and Playwright spawns a full Chromium
    subprocess per request.

    Key insights:
    - Memory overhead: GenD + SigLIP tensors + PIL images per request.
    - CPU contention with concurrent classify/OCR requests.
    - Whether YuNet face detection is stable under load.
    - How many concurrent detect-media requests before OOM.
    """
    weight = 2
    wait_time = between(10, 25)

    @task
    @tag("detect-media", "heavyweight")
    def detect_media(self):
        endpoint = "/detect-media"
        _record_request(endpoint)
        payload = get_random_detect_media_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=endpoint,
            catch_response=True,
            timeout=120,
        ) as response:
            if response.status_code == 400:
                # Expected for some URLs (image download failures, etc.)
                try:
                    detail = response.json().get("detail", "")
                except Exception:
                    detail = response.text[:200] if response.text else ""
                # Only fail if it's NOT a known download issue
                if "download" in detail.lower() or "could not" in detail.lower():
                    response.success()  # Known-acceptable failure
                else:
                    response.failure(f"Bad request: {detail}")
                return

            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200] if response.text else 'no body'}"
                )
                return

            if not _validate_detect_media_response(response, endpoint):
                response.failure("Detect media response validation failed")
                return

            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. MixedTrafficUser — Realistic Combined Traffic Pattern
# ═══════════════════════════════════════════════════════════════════════════════

class MixedTrafficUser(HttpUser):
    """
    Simulates realistic browser extension usage patterns where a single
    user session triggers multiple endpoints in sequence:

    1. Every page visit → /classify (fast, always runs first)
    2. If news → /verify-content (the full pipeline)
    3. Some posts have images → /detect-media
    4. Occasional OCR requests for image-only posts
    5. Periodic health checks

    This is the MOST IMPORTANT test scenario because it reveals contention
    between CPU-bound (classify, OCR, GenD/SigLIP) and I/O-bound
    (verify-content with external API calls) workloads sharing the same
    single-threaded uvicorn event loop.

    Traffic weights approximate real usage:
      classify=50%, verify-content=30%, detect-media=10%, ocr=5%, health=5%
    """
    weight = 5
    wait_time = between(2, 8)

    @task(10)
    @tag("mixed", "classify")
    def classify_then_maybe_verify(self):
        """
        Mimics the extension's flow: classify first, then verify if news.
        This creates a dependent request chain that reveals how the server
        handles back-to-back requests from the same user.
        """
        # Step 1: Classify
        classify_endpoint = "/classify"
        _record_request(classify_endpoint)
        payload = get_news_classify_payload()

        with self.client.post(
            classify_endpoint,
            json=payload,
            name=f"{classify_endpoint} [mixed]",
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Classify failed: HTTP {response.status_code}")
                return

            try:
                data = response.json()
                is_news = data.get("is_news", False)
                _validate_classify_response(response, classify_endpoint)
            except Exception:
                response.failure("Invalid classify response")
                return

            _check_sla(classify_endpoint, response.elapsed.total_seconds() * 1000)
            response.success()

        # Step 2: If classified as news, verify the content
        if is_news:
            time.sleep(0.5)  # Small delay mimicking user interaction
            verify_endpoint = "/verify-content"
            _record_request(verify_endpoint)

            verify_payload = {
                "text": payload["text"],
                "lang": "ar",
            }

            with self.client.post(
                verify_endpoint,
                json=verify_payload,
                name=f"{verify_endpoint} [mixed]",
                catch_response=True,
                timeout=120,
            ) as response:
                if _is_rate_limited(response):
                    _record_rate_limit(verify_endpoint)
                    response.failure("Rate limited")
                    return

                if response.status_code != 200:
                    response.failure(f"Verify failed: HTTP {response.status_code}")
                    return

                _validate_verify_response(response, verify_endpoint)
                _check_sla(verify_endpoint, response.elapsed.total_seconds() * 1000)
                response.success()

    @task(3)
    @tag("mixed", "detect-media")
    def detect_media_flow(self):
        """Single image analysis — represents posts with photos."""
        endpoint = "/detect-media"
        _record_request(endpoint)
        payload = get_random_detect_media_payload()

        with self.client.post(
            endpoint,
            json=payload,
            name=f"{endpoint} [mixed]",
            catch_response=True,
            timeout=120,
        ) as response:
            if response.status_code in (200, 400):
                # 400 is acceptable for image download failures
                _validate_detect_media_response(response, endpoint)
                _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    @tag("mixed", "ocr")
    def ocr_flow(self):
        """OCR request for image-only posts."""
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
                response.failure(f"HTTP {response.status_code}")
                return

            _validate_ocr_response(response, endpoint)
            _check_sla(endpoint, response.elapsed.total_seconds() * 1000)
            response.success()

    @task(1)
    @tag("mixed", "health")
    def health_keepalive(self):
        """Periodic health check — background heartbeat."""
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
