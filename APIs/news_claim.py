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
    classifier = pipeline(
        "zero-shot-classification",
        model="joeddav/xlm-roberta-large-xnli",
        framework="pt"
    )
    print("✅ Model loaded")

class TextRequest(BaseModel):
    text: str

LABELS = [
    "breaking news or official government announcement about politics war economy prices elections disasters infrastructure or current events reported by a media outlet or official source",
    "personal story daily life opinion joke complaint question gossip or casual social media post written by an individual person"
]

@app.post("/classify")
def classify_text(request: TextRequest):
    result = classifier(request.text, LABELS)
    return {
        "label": result["labels"][0],
        "score": float(result["scores"][0]),
        "is_news": result["labels"][0] == "factual news report"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)