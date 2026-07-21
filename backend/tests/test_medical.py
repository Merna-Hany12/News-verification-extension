"""
Test script for the medical info verification pipeline.
Run from the backend directory:
    .venv\Scripts\python.exe tests\test_medical.py
"""
import sys
import os
import asyncio

# Fix Windows console encoding for emoji/Arabic text
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add parent dir so 'backend' package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# ─── Test 1: Medical Keyword Detection ───────────────────────────────────────
def test_medical_detection():
    """Test that the keyword-based detector identifies medical text."""
    from backend.nodes.classify import _detect_medical

    print("\n" + "="*60)
    print("TEST 1: Medical Keyword Detection")
    print("="*60)

    test_cases = [
        # (text, expected_is_medical)
        ("التدخين يسبب سرطان الرئة", True),
        ("شرب الماء الساخن يعالج السرطان", True),
        ("فيتامين سي يمنع الإصابة بالبرد", True),
        ("Smoking causes lung cancer", True),
        ("Drinking bleach cures COVID-19", True),
        ("Vitamin C prevents the common cold", True),
        ("رئيس مصر يزور فرنسا", False),       # news, not medical
        ("The stock market crashed today", False),  # news, not medical
        ("هل تعلم أن الأرض كروية", False),      # science, not medical
    ]

    all_passed = True
    for text, expected in test_cases:
        result = _detect_medical(text)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_passed = False
        print(f"  {status} '{text[:50]}' → medical={result} (expected={expected})")

    return all_passed


# ─── Test 2: Keyword Extraction ──────────────────────────────────────────────
def test_keywords():
    """Test keyword extraction for medical text."""
    from backend.core.text_processing import _extract_keywords

    print("\n" + "="*60)
    print("TEST 2: Medical Keyword Extraction")
    print("="*60)

    test_cases = [
        "التدخين يسبب سرطان الرئة ويزيد من خطر أمراض القلب",
        "Smoking causes lung cancer and increases heart disease risk",
        "شرب الماء الساخن يعالج السرطان",
    ]

    for text in test_cases:
        keywords = _extract_keywords(text)
        print(f"  Text: '{text[:60]}'")
        print(f"  Keywords: '{keywords}'")
        print()

    return True


# ─── Test 3: PubMed Fetcher ──────────────────────────────────────────────────
async def test_pubmed():
    """Test that PubMed fetcher returns relevant medical articles."""
    import httpx
    from backend.search.fetchers import _fetch_pubmed

    print("\n" + "="*60)
    print("TEST 3: PubMed Fetcher")
    print("="*60)

    queries = [
        ("smoking lung cancer", "en"),
        ("vitamin C cold prevention", "en"),
        ("bleach ingestion danger", "en"),
    ]

    all_passed = True
    async with httpx.AsyncClient() as client:
        for query, lang in queries:
            articles = await _fetch_pubmed(client, query, lang)
            count = len(articles)
            status = "✅" if count > 0 else "❌"
            if count == 0:
                all_passed = False
            print(f"\n  {status} Query: '{query}' → {count} articles")
            for a in articles[:3]:
                print(f"     📄 {a['title'][:80]}")
                print(f"        Source: {a['source_name']}")
                print(f"        Link: {a['link']}")
            await asyncio.sleep(0.5)

    return all_passed


# ─── Test 4: Full Pipeline ───────────────────────────────────────────────────
async def test_full_pipeline():
    """Test the full verify pipeline with medical claims."""
    from backend.core.config import GROQ_API_KEY

    if not GROQ_API_KEY:
        print("\n  Skipping full pipeline test — GROQ_API_KEY not set")
        return True

    print("\n" + "="*60)
    print("TEST 4: Full Medical Verification Pipeline")
    print("="*60)

    from backend.nodes import classify
    from backend.graph.builder import build_graph, run_verify

    if classify.classifier is None:
        print("  Loading SetFit classifier model...")
        from setfit import SetFitModel
        classify.classifier = SetFitModel.from_pretrained("darck-12/news-classification-minilm")
        print("  Classifier loaded")

    graph = build_graph()

    test_claims = [
        {
            "text": "Smoking causes lung cancer",
            "lang": "en",
            "desc": "Well-established medical FACT",
        },
        {
            "text": "التدخين يسبب سرطان الرئة",
            "lang": "ar",
            "desc": "Same claim in Arabic — should be FACT",
        },
        {
            "text": "Drinking bleach cures COVID-19",
            "lang": "en",
            "desc": "Dangerous misinformation — should be FAKE or UNVERIFIED",
        },
        {
            "text": "Vitamin C prevents the common cold",
            "lang": "en",
            "desc": "Contested claim — should be UNVERIFIED",
        },
    ]

    for case in test_claims:
        print(f"\n  -- Claim: '{case['text']}'")
        print(f"     ({case['desc']})")

        result = await run_verify(graph, case["text"], case["lang"])

        verdict = result.get("verdict", "?")
        confidence = result.get("confidence", 0)
        explanation = result.get("explanation", "")
        sources = result.get("sources", [])

        print(f"     -> Verdict: {verdict} (confidence: {confidence:.2f})")
        print(f"        Explanation: {explanation[:120]}")
        print(f"        Sources: {len(sources)}")
        for s in sources[:3]:
            trusted_tag = "TRUSTED" if s.get("trusted") else "untrusted"
            print(f"          [{trusted_tag}] {s.get('title', '')[:70]}")

    return True


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print("=== HAQQ Medical Verification — Test Suite ===")
    print("=" * 60)

    # Lightweight tests first
    test_medical_detection()
    test_keywords()
    await test_pubmed()

    # Full pipeline (loads classifier model ~2-3 min first time)
    print("\n  Loading classifier model (takes a few minutes the first time)...")
    await test_full_pipeline()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
