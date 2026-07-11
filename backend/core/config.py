import os
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

loaded = load_dotenv(ENV_FILE)

print("Loading:", ENV_FILE)
print("Loaded:", loaded)
NEWSDATA_KEY = os.environ.get("NEWSDATA_API_KEY", "")
CURRENTS_KEY = os.environ.get("CURRENTS_API_KEY", "")
GNEWS_KEY    = os.environ.get("GNEWS_API_KEY",    "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY",     "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL",       "llama-3.3-70b-versatile")

# How many chars of article body to pass to the LLM per article
BODY_CHARS_PER_ARTICLE = 800
# How many articles to actually fetch the body for (costs time)
BODY_FETCH_TOP_N = 3

# Query character limits
API_QUERY_LIMIT    = 95
# Let's search query limit also be defined in config
SEARCH_QUERY_LIMIT = 200

# ─── TRUSTED SOURCES ─────────────────────────────────────────────────────────
TRUSTED: set[str] = {
    # International
    "bbc", "reuters", "ap", "apnews", "associated press",
    "aljazeera", "al jazeera", "cnn", "nytimes", "theguardian",
    "france24", "dw", "euronews", "skynews", "sky news", "afp",
    "washingtonpost", "wsj", "bloomberg", "time", "newsweek",
    "nbcnews", "cbsnews", "abcnews", "usatoday", "latimes",
    "independent", "telegraph", "ft", "middleeasteye",
    "npr", "pbs", "axios", "politico", "propublica",
    "economist", "forbes", "cnbc", "marketwatch", "businessinsider",

    # Fact-checking
    "snopes", "politifact", "factcheck", "fullfact",

    # Scientific / encyclopedic / health
    "wikipedia", "britannica", "nature", "sciencedirect", "pubmed",
    "ncbi", "nih", "who", "nasa", "arxiv", "scholar",
    "thelancet", "nejm", "cdc", "esa", "sciencemag", "aaas", "jpl",

    # Arabic
    "الجزيرة", "رويترز", "العربية", "alarabiya", "france24arabic",
    "aawsat", "asharqalawsat", "alhurra",
    "skynewsarabia", "bbcarabic", "cnnarabic", "independentarabia",
    "alain", "emaratalyoum", "alsharq", "annahar",

    # Egyptian
    "ahram", "alahram", "youm7", "masrawy", "elwatannews",
    "almasryalyoum", "shorouk", "elshorouk", "vetogate",
    "filbalad", "mobtada", "dotmsr", "elbashayer", "cairo24",
    "sis", "egypttoday", "dailynewsegypt", "ahramonline",
}

# ─── MEDICAL TRUSTED SOURCES ─────────────────────────────────────────────────
MEDICAL_TRUSTED: set[str] = {
    # Government / institutional
    "who", "cdc", "nih", "ncbi", "pubmed", "medlineplus", "fda",
    "ema", "nhs", "mhra",

    # Peer-reviewed journals
    "thelancet", "nejm", "bmj", "jama", "nature", "sciencedirect",
    "cochrane", "pubmed", "ncbi", "plos", "frontiersin",

    # Trusted medical info sites
    "mayoclinic", "clevelandclinic", "hopkinsmedicine", "webmd",
    "healthline", "medscape", "uptodate", "drugs.com",

    # Arabic medical
    "webteb", "altibbi", "dailymedicalinfo", "sehatok",
}
# Labels for the three-class zero-shot classifier
# NOTE: Medical detection is handled separately via keyword matching (MEDICAL_KEYWORDS)
# because the zero-shot model can't reliably separate "medical" from "historical/scientific".
CLASSIFY_LABELS = [
    # class 0 — news
    "breaking news report journalism media coverage current event announcement politics",
    # class 1 — historical / scientific
    "historical fact scientific discovery research study academic ancient history science",
    # class 2 — non-news
    "personal opinion joke meme social media post casual conversation gossip advertisement",
]

# Map label index → internal content_type string
LABEL_TO_TYPE = {
    0: "news",
    1: "historical_scientific",
    2: "non_news",
}

# ─── MEDICAL KEYWORD DETECTION ──────────────────────────────────────────────────
# Used as a secondary check after zero-shot classification.
# If enough medical keywords are found, content_type is overridden to "medical".
MEDICAL_KEYWORDS_EN: set[str] = {
    # Diseases & conditions
    "cancer", "diabetes", "disease", "infection", "syndrome", "disorder",
    "tumor", "stroke", "asthma", "allergy", "arthritis", "pneumonia",
    "flu", "influenza", "covid", "coronavirus", "hiv", "aids", "malaria",
    "cholesterol", "hypertension", "obesity", "anemia", "dementia",
    "alzheimer", "parkinson", "epilepsy", "hepatitis",
    # Treatments & medicine
    "treatment", "therapy", "drug", "medication", "antibiotic", "vaccine",
    "surgery", "prescription", "dose", "dosage", "pill", "injection",
    "chemotherapy", "radiation", "transplant", "clinical",
    # Body & health
    "symptom", "diagnosis", "patient", "doctor", "hospital", "medical",
    "health", "cure", "healing", "medicine", "pharmaceutical",
    "blood", "heart", "lung", "liver", "kidney", "brain",
    "immune", "immunity", "vitamin", "protein", "nutrition",
    # Actions
    "smoking", "drinking", "exercise", "diet", "fasting",
    "prevent", "prevention", "causes", "risk", "side effect",
    "bleach", "toxic", "poison", "overdose", "fatal",
}

MEDICAL_KEYWORDS_AR: set[str] = {
    # أمراض وحالات
    "سرطان", "مرض", "عدوى", "إصابة", "التهاب", "فيروس",
    "كورونا", "السكري", "ضغط", "قلب", "رئة", "كبد",
    "كلى", "الزهايمر", "جلطة", "سكتة", "ربو", "حساسية",
    "انفلونزا", "إيدز", "ملاريا", "ورم", "أورام",
    # علاج وأدوية
    "علاج", "دواء", "مضاد", "لقاح", "عملية", "جراحة",
    "أشعة", "كيماوي", "جرعة", "حقنة", "دوائي",
    # صحة وجسم
    "طبي", "مريض", "مستشفى", "صحة", "صحي",
    "فيتامين", "تغذية", "مناعة", "أعراض", "تشخيص",
    "دم", "مخ", "عظام", "أعصاب",
    # سلوكيات
    "تدخين", "سجائر", "رياضة", "حمية", "صيام",
    "وقاية", "خطر", "سموم", "تسمم",
    # كلمات مفتاحية
    "يعالج", "يسبب", "يشفي", "يقي", "يمنع",
}

# Minimum number of medical keywords required to classify as medical
MEDICAL_KEYWORD_THRESHOLD = 2
