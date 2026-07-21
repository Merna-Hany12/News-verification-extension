"""
Realistic test payloads for load testing the HAQQ News Verification backend.

Each payload set is designed to exercise different classification branches,
language paths, and edge cases that the pipeline handles in production.
All text payloads are drawn from the kinds of claims the browser extension
encounters on Arabic and English social media.
"""

import random

# ═══════════════════════════════════════════════════════════════════════════════
# /classify — TextRequest payloads
# Expected: SetFit 4-class classification (news / historical_scientific /
#            medical / non_news)
# ═══════════════════════════════════════════════════════════════════════════════

CLASSIFY_PAYLOADS = [
    # ── Arabic news (expected: news) ──────────────────────────────────────────
    {
        "text": "أعلن الرئيس المصري عبد الفتاح السيسي عن مشروع جديد لتوسيع قناة السويس بتكلفة 8 مليارات دولار لتعزيز التجارة العالمية",
        "expected_label": "news",
    },
    {
        "text": "وزارة الصحة السعودية تعلن عن تسجيل أكثر من مليون حالة تطعيم ضد كورونا خلال الأسبوع الماضي في جميع مناطق المملكة",
        "expected_label": "news",
    },
    {
        "text": "الجيش الإسرائيلي يعلن عن عملية عسكرية واسعة في شمال غزة وسط قصف مكثف على المناطق السكنية",
        "expected_label": "news",
    },
    {
        "text": "ارتفاع أسعار الذهب عالمياً لتسجل أعلى مستوى في تاريخها عند 2500 دولار للأونصة وسط مخاوف من الركود الاقتصادي",
        "expected_label": "news",
    },

    # ── English news (expected: news) ─────────────────────────────────────────
    {
        "text": "NASA confirms discovery of water ice deposits on the lunar south pole, potentially enabling future human settlements on the Moon",
        "expected_label": "news",
    },
    {
        "text": "The European Central Bank announces emergency interest rate cut amid growing fears of recession across the eurozone",
        "expected_label": "news",
    },

    # ── Arabic medical (expected: medical) ────────────────────────────────────
    {
        "text": "دراسة علمية جديدة: شرب الماء الساخن مع الليمون صباحاً يقي من السرطان ويعالج السكري نهائياً",
        "expected_label": "medical",
    },
    {
        "text": "وزارة الصحة تحذر من تناول المضادات الحيوية بدون وصفة طبية لخطورتها على المناعة والكبد",
        "expected_label": "medical",
    },

    # ── English medical (expected: medical) ───────────────────────────────────
    {
        "text": "Drinking bleach cures COVID-19 according to a viral social media post that has been shared millions of times",
        "expected_label": "medical",
    },
    {
        "text": "New clinical trial shows vitamin D supplements reduce cancer risk by 40 percent in elderly patients over five years",
        "expected_label": "medical",
    },

    # ── Historical / scientific (expected: historical_scientific) ─────────────
    {
        "text": "الأهرامات المصرية بنيت بواسطة كائنات فضائية وليس بأيدي العمال المصريين القدماء حسب نظرية مؤامرة منتشرة",
        "expected_label": "historical_scientific",
    },
    {
        "text": "العالم نيكولا تسلا اخترع جهازاً يولد طاقة مجانية لكن الحكومة الأمريكية صادرته وأخفته عن العالم",
        "expected_label": "historical_scientific",
    },

    # ── Non-news / opinions (expected: non_news) ─────────────────────────────
    {
        "text": "أعتقد أن الحكومة مقصرة في ملف التعليم والصحة ويجب محاسبة المسؤولين عن هذا الإهمال الواضح",
        "expected_label": "non_news",
    },
    {
        "text": "I think this new policy is absolutely terrible and will destroy the economy. Politicians never listen to the people.",
        "expected_label": "non_news",
    },
    {
        "text": "صباح الخير يا أصدقائي، أتمنى لكم يوماً سعيداً مليئاً بالإنجازات والنجاح",
        "expected_label": "non_news",
    },

    # ── Edge cases ────────────────────────────────────────────────────────────
    {
        "text": "مرحبا",  # Very short text (<20 chars) → should return non_news/unverified
        "expected_label": "non_news",
    },
    {
        "text": "x" * 600,  # Very long repetitive text (exceeds 500-char truncation)
        "expected_label": "non_news",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# /verify-content — VerifyContentRequest payloads
# Expected: Full pipeline (classify → keywords → search → fetch → LLM → score)
# ═══════════════════════════════════════════════════════════════════════════════

VERIFY_CONTENT_PAYLOADS = [
    # ── Arabic news claims (text-only) ────────────────────────────────────────
    {
        "text": "أعلنت وزارة التربية والتعليم المصرية تأجيل امتحانات الثانوية العامة لمدة أسبوعين بسبب موجة الحر الشديدة",
        "lang": "ar",
        "expected_verdict_type": "news_claim",
    },
    {
        "text": "اكتشاف مدينة فرعونية كاملة تحت رمال الصحراء الغربية في مصر تعود لأكثر من 5000 سنة",
        "lang": "ar",
        "expected_verdict_type": "news_claim",
    },
    {
        "text": "الأمم المتحدة تعلن رسمياً أن مصر حققت الاكتفاء الذاتي من القمح لأول مرة في تاريخها",
        "lang": "ar",
        "expected_verdict_type": "news_claim",
    },

    # ── English news claims (text-only) ───────────────────────────────────────
    {
        "text": "Breaking: Japan successfully launches the first space elevator prototype, revolutionizing orbital transport",
        "lang": "en",
        "expected_verdict_type": "news_claim",
    },
    {
        "text": "The World Health Organization declares a global health emergency due to a new respiratory virus outbreak in Southeast Asia",
        "lang": "en",
        "expected_verdict_type": "news_claim",
    },

    # ── Medical claims ────────────────────────────────────────────────────────
    {
        "text": "دراسة: تناول الثوم على الريق يعالج ضغط الدم المرتفع ويخفض الكولسترول بنسبة 50 بالمئة خلال شهر",
        "lang": "ar",
        "expected_verdict_type": "medical_claim",
    },
    {
        "text": "Scientists confirm that 5G radiation causes brain tumors and neurological damage in humans",
        "lang": "en",
        "expected_verdict_type": "medical_claim",
    },

    # ── Opinion / non-news (should short-circuit) ─────────────────────────────
    {
        "text": "في رأيي المنتخب المصري لن يتأهل لكأس العالم القادمة بسبب ضعف الإدارة الفنية",
        "lang": "ar",
        "expected_verdict_type": "opinion",
    },

    # ── Text + image_url (triggers concurrent OCR path) ───────────────────────
    {
        "text": "صورة متداولة لانهيار مبنى سكني في القاهرة بسبب الزلزال",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/Cairo_Tower_from_the_south.jpg/800px-Cairo_Tower_from_the_south.jpg",
        "lang": "ar",
        "expected_verdict_type": "news_with_image",
    },

    # ── Short/empty text + image (triggers OCR-first path) ────────────────────
    {
        "text": "",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/All_Gizah_Pyramids.jpg/800px-All_Gizah_Pyramids.jpg",
        "lang": "ar",
        "expected_verdict_type": "ocr_only",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# /ocr — ImageRequest payloads
# Expected: EasyOCR text extraction from images
# ═══════════════════════════════════════════════════════════════════════════════

OCR_PAYLOADS = [
    # Publicly accessible images with text content
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/Cairo_Tower_from_the_south.jpg/800px-Cairo_Tower_from_the_south.jpg",
        "description": "Cairo Tower — may contain minimal text",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/All_Gizah_Pyramids.jpg/800px-All_Gizah_Pyramids.jpg",
        "description": "Pyramids of Giza — likely no text",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Google_2015_logo.svg/800px-Google_2015_logo.svg.png",
        "description": "Google logo — has English text 'Google'",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# /detect-media — DetectMediaRequest payloads
# Expected: Face detection + GenD deepfake + SigLIP AIGC analysis
# ═══════════════════════════════════════════════════════════════════════════════

DETECT_MEDIA_PAYLOADS = [
    # ── Single image analysis (simplest path, no Playwright) ──────────────────
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Mahatma-Gandhi%2C_studio%2C_1931.jpg/800px-Mahatma-Gandhi%2C_studio%2C_1931.jpg",
        "description": "Historical photo with face — should detect face, classify as real",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/All_Gizah_Pyramids.jpg/800px-All_Gizah_Pyramids.jpg",
        "description": "Landscape without faces — should rely on AIGC model only",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg/800px-Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg",
        "description": "Mona Lisa — painting with face, interesting edge case for AI detection",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_random_classify_payload() -> dict:
    """Returns a random /classify payload as {text: str}."""
    item = random.choice(CLASSIFY_PAYLOADS)
    return {"text": item["text"]}


def get_random_verify_payload() -> dict:
    """Returns a random /verify-content payload as {text, lang, ?image_url}."""
    item = random.choice(VERIFY_CONTENT_PAYLOADS)
    payload = {"text": item.get("text", ""), "lang": item.get("lang", "ar")}
    if "image_url" in item:
        payload["image_url"] = item["image_url"]
    return payload


def get_random_ocr_payload() -> dict:
    """Returns a random /ocr payload as {image_url: str}."""
    item = random.choice(OCR_PAYLOADS)
    return {"image_url": item["image_url"]}


def get_random_detect_media_payload() -> dict:
    """Returns a random /detect-media payload as {image_url: str}."""
    item = random.choice(DETECT_MEDIA_PAYLOADS)
    return {"image_url": item["image_url"]}


def get_news_classify_payload() -> dict:
    """Returns a random classify payload that is expected to be classified as news."""
    news_items = [p for p in CLASSIFY_PAYLOADS if p["expected_label"] == "news"]
    item = random.choice(news_items)
    return {"text": item["text"]}
