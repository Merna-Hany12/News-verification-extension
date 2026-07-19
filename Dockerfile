# ─── Stage: Production GPU image ─────────────────────────────────────────────
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ─── System dependencies ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    ffmpeg curl \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Python dependencies (cached layer) ──────────────────────────────────────
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# ─── Application code ────────────────────────────────────────────────────────
COPY backend/ ./backend/

# ─── Pre-download ML models into the image (avoids cold-start downloads) ─────
RUN python -c "from transformers import pipeline; pipeline('zero-shot-classification', model='MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7')"
RUN python -c "import easyocr; easyocr.Reader(['ar', 'en'], gpu=False)"

# ─── Expose and run ──────────────────────────────────────────────────────────
EXPOSE 8000
CMD ["python", "-m", "backend.main"]
