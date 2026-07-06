from backend.core.text_processing import _normalise, _extract_keywords

def test_normalise():
    assert _normalise("بِسْمِ اللَّهِ") == "بسم الله"
    assert _normalise("الأخبار") == "الاخبار"

def test_extract_keywords():
    assert _extract_keywords("هذا الخبر عاجل الآن") == "الخبر الان"
    assert _extract_keywords("تفاصيل الحدث في مصر") == "تفاصيل الحدث مصر"
