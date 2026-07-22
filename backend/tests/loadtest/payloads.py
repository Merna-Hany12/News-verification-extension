"""
Realistic test payloads for load testing the HAQQ News Verification backend.

Contains payloads for the 3 credit-free endpoints:
  1. /health    (no payload needed)
  2. /classify  (TextRequest payloads for SetFit model)
  3. /ocr       (ImageRequest payloads for EasyOCR model)
"""

import random

# ═══════════════════════════════════════════════════════════════════════════════
# /classify — TextRequest payloads
# Expected: SetFit 4-class classification (news / historical_scientific /
#            medical / non_news)
# ═══════════════════════════════════════════════════════════════════════════════

CLASSIFY_PAYLOADS = [
    # ── Arabic news ──────────────────────────────────────────────────────────
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

    # ── English news ─────────────────────────────────────────────────────────
    {
        "text": "NASA confirms discovery of water ice deposits on the lunar south pole, potentially enabling future human settlements on the Moon",
        "expected_label": "news",
    },
    {
        "text": "The European Central Bank announces emergency interest rate cut amid growing fears of recession across the eurozone",
        "expected_label": "news",
    },

    # ── Arabic medical ────────────────────────────────────────────────────────
    {
        "text": "دراسة علمية جديدة: شرب الماء الساخن مع الليمون صباحاً يقي من السرطان ويعالج السكري نهائياً",
        "expected_label": "medical",
    },
    {
        "text": "وزارة الصحة تحذر من تناول المضادات الحيوية بدون وصفة طبية لخطورتها على المناعة والكبد",
        "expected_label": "medical",
    },

    # ── English medical ───────────────────────────────────────────────────────
    {
        "text": "Drinking bleach cures COVID-19 according to a viral social media post that has been shared millions of times",
        "expected_label": "medical",
    },
    {
        "text": "New clinical trial shows vitamin D supplements reduce cancer risk by 40 percent in elderly patients over five years",
        "expected_label": "medical",
    },

    # ── Historical / scientific ───────────────────────────────────────────────
    {
        "text": "الأهرامات المصرية بنيت بواسطة كائنات فضائية وليس بأيدي العمال المصريين القدماء حسب نظرية مؤامرة منتشرة",
        "expected_label": "historical_scientific",
    },
    {
        "text": "العالم نيكولا تسلا اخترع جهازاً يولد طاقة مجانية لكن الحكومة الأمريكية صادرته وأخفته عن العالم",
        "expected_label": "historical_scientific",
    },

    # ── Non-news / opinions ───────────────────────────────────────────────────
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
        "text": "مرحبا",  # Very short text (<20 chars)
        "expected_label": "non_news",
    },
    {
        "text": "x" * 600,  # Long text exceeding 500-char truncation
        "expected_label": "non_news",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# /ocr — ImageRequest payloads
# Expected: EasyOCR text extraction from images
# ═══════════════════════════════════════════════════════════════════════════════

OCR_PAYLOADS = [
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/Cairo_Tower_from_the_south.jpg/800px-Cairo_Tower_from_the_south.jpg",
        "description": "Cairo Tower photo",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/All_Gizah_Pyramids.jpg/800px-All_Gizah_Pyramids.jpg",
        "description": "Pyramids of Giza photo",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Google_2015_logo.svg/800px-Google_2015_logo.svg.png",
        "description": "Google logo image with text",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_random_classify_payload() -> dict:
    """Returns a random /classify payload as {text: str}."""
    item = random.choice(CLASSIFY_PAYLOADS)
    return {"text": item["text"]}


def get_random_ocr_payload() -> dict:
    """Returns a random /ocr payload as {image_url: str}."""
    item = random.choice(OCR_PAYLOADS)
    return {"image_url": item["image_url"]}
