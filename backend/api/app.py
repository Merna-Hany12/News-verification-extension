from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from transformers import pipeline
import easyocr

from backend.graph.builder import build_graph
from backend.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("LOADING CLASSIFIER...")
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    )
    app.state.classifier = classifier
    print("CLASSIFIER LOADED ✅")

    # Break the circular import by injecting the model directly into classify node's module
    from backend.nodes import classify
    classify.classifier = classifier
    print("CLASSIFIER INJECTED TO GRAPH NODE ✅")

    print("LOADING EASYOCR (ar + en)...")
    ocr_reader = easyocr.Reader(["ar", "en"], gpu=False)
    app.state.ocr_reader = ocr_reader
    print("EASYOCR LOADED ✅")

    print("BUILDING LANGGRAPH PIPELINE...")
    app.state.haqq_graph = build_graph()
    print("LANGGRAPH PIPELINE READY ✅")

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
