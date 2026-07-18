from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from transformers import pipeline
import easyocr

from backend.graph.builder import build_graph
from backend.api.routes import router
from backend.api.detect_media import router as media_router

import cv2
import os
from backend.models.gend import GenD
import torch


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

    print("LOADING YUNET FACE DETECTOR...")
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yunet_path = os.path.join(base_dir, "models", "face_detection_yunet_2023mar.onnx")
    # Create YuNet (size is dummy here, will be updated per-frame later)
    yunet = cv2.FaceDetectorYN.create(
        model=yunet_path, config="", input_size=(320, 320),
        score_threshold=0.6, nms_threshold=0.3, top_k=5000
    )
    app.state.yunet = yunet
    print("LOADING DEEPFAKE MODEL (GenD)...")
    from huggingface_hub import hf_hub_download
    from backend.models.gend import GenDConfig, GenD
    import torch
    
    # 1. Fetch the config and initialize an empty model (bypasses the meta device crash)
    config = GenDConfig.from_pretrained("yermandy/GenD_CLIP_L_14")
    gend_model = GenD(config)
    
    # 2. Download and load the weights manually
    try:
        weights_path = hf_hub_download(repo_id="yermandy/GenD_CLIP_L_14", filename="pytorch_model.bin")
        state_dict = torch.load(weights_path, map_location="cpu")
    except Exception:
        # Fallback in case the repo uses safetensors instead of bin
        from safetensors.torch import load_file
        weights_path = hf_hub_download(repo_id="yermandy/GenD_CLIP_L_14", filename="model.safetensors")
        state_dict = load_file(weights_path)
    # 3. Apply weights and set to CPU
    gend_model.load_state_dict(state_dict)
    gend_model.eval()
    gend_model.to("cpu")
    
    app.state.gend_model = gend_model


    print("LOADING AIGC MODEL (SigLIP)...")
    aigc_pipeline = pipeline("image-classification", model="Ateeqq/ai-vs-human-image-detector")
    app.state.aigc_pipeline = aigc_pipeline
    print("ALL MODELS LOADED ✅")

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(media_router)
