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
    # Scientific / encyclopedic (relevant for historical_sci class)
    "wikipedia", "britannica", "nature", "sciencedirect", "pubmed",
    "ncbi", "nih", "who", "nasa", "arxiv", "scholar",
    # Arabic
    "الجزيرة", "رويترز", "العربية", "alarabiya", "france24arabic",
    "aawsat", "asharqalawsat", "alhurra",
    # Egyptian
    "ahram", "alahram", "youm7", "masrawy", "elwatannews",
    "almasryalyoum", "shorouk", "elshorouk", "vetogate",
    "filbalad", "mobtada", "dotmsr", "elbashayer", "cairo24",
}

# Labels for the three-class zero-shot classifier
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
