import sys
import asyncio

 
# MUST run before any other import that could touch asyncio/uvicorn's
# event loop — Playwright launches Chromium as a subprocess, which on
# Windows requires the Proactor event loop (the default Selector loop
# does not implement asyncio.create_subprocess_exec). Setting this here,
# as the very first lines of the entire module, is the most reliable
# place for it to actually take effect regardless of how this app is
# launched (uvicorn CLI, uv run, --reload, etc).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
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
os.environ["USE_TF"] = "0"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
from backend.models.gend import GenD
import torch


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("LOADING CLASSIFIER (SetFit)...")
    from setfit import SetFitModel
    classifier = SetFitModel.from_pretrained("darck-12/news-classification-minilm")
    app.state.classifier = classifier
    print("CLASSIFIER LOADED ✅")

    # Break the circular import by injecting the model directly into classify node's module
    from backend.nodes import classify
    classify.classifier = classifier
    print("CLASSIFIER INJECTED TO GRAPH NODE ✅")

    print("LOADING EASYOCR (ar + en)...")
    ocr_reader = easyocr.Reader(["ar", "en"], gpu=False, verbose=False)
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

    print("LOADING CONVNEXT AI-VS-HUMAN MODEL...")
    import timm
    import torchvision.transforms as T
    
    CONVNEXT_REPO_ID = "xRayon/convnext-ai-images-detector"
    CONVNEXT_CKPT_FILENAME = "AI Images Detector/checkpoints/checkpoint_phase2.pth"
    
    try:
        convnext_ckpt_path = hf_hub_download(repo_id=CONVNEXT_REPO_ID, filename=CONVNEXT_CKPT_FILENAME)
        convnext_model = timm.create_model("convnextv2_base", pretrained=False, num_classes=2)
        ckpt = torch.load(convnext_ckpt_path, map_location="cpu")
        convnext_model.load_state_dict(ckpt["model"])
        convnext_model.eval()
        convnext_model.to("cpu")
        app.state.convnext_model = convnext_model
        print("CONVNEXT MODEL LOADED ✅")
    except Exception as e:
        print(f"FAILED TO LOAD CONVNEXT MODEL: {e}")
        raise e
        
    _convnext_mean = (0.485, 0.456, 0.406)
    _convnext_std = (0.229, 0.224, 0.225)
    convnext_transform = T.Compose([
        T.Resize(288, interpolation=T.InterpolationMode.LANCZOS),
        T.CenterCrop(256),
        T.ToTensor(),
        T.Normalize(_convnext_mean, _convnext_std),
    ])
    app.state.convnext_transform = convnext_transform

    print("ALL MODELS LOADED ✅")

    yield

    # Ensure any remaining events in the buffer are flushed to Axiom on shutdown
    from backend.observability.axiom_logger import axiom_logger
    print("FLUSHING AXIOM LOGGER...")
    try:
        await axiom_logger.flush()
    except Exception as e:
        print(f"Error flushing Axiom logger on shutdown: {e}")


from backend.observability.langsmith_config import setup_langsmith
from backend.observability.middleware import ObservabilityMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from backend.api.rate_limiter import limiter

setup_langsmith()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(ObservabilityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(media_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

