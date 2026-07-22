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
        "image_url": "https://scontent.fcai19-5.fna.fbcdn.net/v/t39.99422-6/752881070_1377623294252908_2406409165708529296_n.png?stp=dst-jpg_tt6&cstp=mx1080x1080&ctp=s1080x1080&_nc_cat=1&ccb=1-7&_nc_sid=127cfc&_nc_ohc=r7wrs6SuFo0Q7kNvwEVU9Uj&_nc_oc=AdqqdQbsUcZqGMwCXa7Dw3j90ZoGgrQvTuXDn8T_M5P0EQmx6jlvjPIDo6CVhHUOQN0&_nc_zt=14&_nc_ht=scontent.fcai19-5.fna&_nc_gid=fgMWFAqy1APt5J_Xp0LIog&_nc_ss=7b2a8&oh=00_AQAZMnXqokHro0CTFBWFPQmjx3IDev0GDBLDZil9vtkL2Q&oe=6A65FF47",
        "description": "Introducing Gemini 3.6 Flash, 3.5 Flash-Lite, and 3.5 Flash Cyber ٨",
    },
    {
        "image_url": "https://scontent.fcai19-5.fna.fbcdn.net/v/t39.99422-6/751871162_1749052532782904_3755488305713744429_n.png?stp=dst-jpg_tt6&cstp=mx1440x1800&ctp=s1440x1800&_nc_cat=103&ccb=1-7&_nc_sid=833d8c&_nc_ohc=p7dDW_1wXMoQ7kNvwEo2STX&_nc_oc=Adq8X5u7ZcviUQkG-k0T2LMb-1VyBk57zUZCsv2ymSSE-P980H0DjxZ4sbFCA4iMlGw&_nc_zt=14&_nc_ht=scontent.fcai19-5.fna&_nc_gid=8INnRDUf45hM1sGEyGQlAA&_nc_ss=7b2a8&oh=00_AQAwig-4muMdpu0L6wNU7lFYJU_flup5hjolrrZKtRjJ5Q&oe=6A6609DF",
        "description": "عاجل سفن تبدأ تسجيل طواقمها لدى صنعاء لعبور المندب Onewsvemeny",
    },
    {
        "image_url": "https://scontent.fcai19-5.fna.fbcdn.net/v/t39.99422-6/748527735_1039977028589358_8838463850821017904_n.png?stp=dst-jpg_tt6&cstp=mx1764x2048&ctp=s1764x2048&_nc_cat=103&ccb=1-7&_nc_sid=833d8c&_nc_ohc=ehyvNg2zBrsQ7kNvwHyycW0&_nc_oc=AdqOTD6fks4PqOcPckJy6wBhCF5-BjRUfcHP7tdCpOsFEaTXFqnr_jqp5UrRxY7wm-E&_nc_zt=14&_nc_ht=scontent.fcai19-5.fna&_nc_gid=2Xu62SInzixw57wklQxXPQ&_nc_ss=782a8&oh=00_AQD_duEHmLOzFYgVgiWQJTEkK4wac0jwcR8ode-r-7U7uQ&oe=6A660BA2",
        "description": "اخبار سريعة الشرع برسالق حازمة : نمديد  السلام ., وسيوف دمشق حادة",
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
