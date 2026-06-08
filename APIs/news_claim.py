from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from transformers import pipeline

# ─── MODELS ───────────────────────────────────────────────
classifier = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global classifier   # ← only classifier, no summarizer globals

    print("LOADING CLASSIFIER...")
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    )
    print("CLASSIFIER LOADED ✅")

    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── SCHEMA ───────────────────────────────────────────────
class TextRequest(BaseModel):   # ← was missing — caused the validation error
    text: str

# ─── LABELS ───────────────────────────────────────────────
LABELS = [
    "news report breaking news journalism media coverage event announcement",
    "personal opinion social media post joke gossip casual conversation"
]

# ─── CLASSIFY ─────────────────────────────────────────────
@app.post("/classify")
def classify_text(request: TextRequest):   # ← was missing type hint
    result = classifier(request.text, LABELS)

    news_score     = 0.0
    non_news_score = 0.0

    for label, score in zip(result["labels"], result["scores"]):
        if "news" in label.lower() or "report" in label.lower():
            news_score = float(score)
        else:
            non_news_score = float(score)

    print(f"[HAQQ] news_score    : {news_score:.3f}")
    print(f"[HAQQ] non_news_score: {non_news_score:.3f}")
    print(f"[HAQQ] text          : {request.text[:80]}")

    is_news = news_score > non_news_score and news_score >= 0.50

    return {
        "label":          result["labels"][0],
        "score":          float(result["scores"][0]),
        "news_score":     news_score,
        "non_news_score": non_news_score,
        "is_news":        is_news
    }

# ─── RUN ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)