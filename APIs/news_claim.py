from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from transformers import pipeline

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

classifier = None

@app.on_event("startup")
def load_model():
    global classifier
    print("LOADING MODEL...")
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    )
    print("MODEL LOADED")
class TextRequest(BaseModel):
    text: str

LABELS = [
    "breaking news or official government announcement about politics war economy prices elections disasters infrastructure",
    "personal story daily life opinion joke complaint question gossip or casual social media post written by an individual person"
]

@app.post("/classify")
def classify_text(request: TextRequest):
    result = classifier(request.text, LABELS)

    top_label = result["labels"][0]
    top_score = float(result["scores"][0])

    is_news = (
        "breaking news" in top_label.lower()
        and top_score > 0.5
    )

    return {
        "label": top_label,
        "score": top_score,
        "is_news": is_news
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)