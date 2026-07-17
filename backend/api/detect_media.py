import os
import tempfile
import cv2
import numpy as np
import torch
import httpx
from PIL import Image
from fastapi import APIRouter, Request, HTTPException

from backend.api.schemas import DetectMediaRequest

router = APIRouter()

# ─── 1. Face-Aware Fusion Logic ───
def face_aware_fusion(avg_df_prob: float, avg_ai_prob: float, pct_faces_detected: float):
    """
    Face-detection-aware fusion.
    If faces are meaningfully present, trust the deepfake model first.
    Fall back to AIGC model only when no faces are detected.
    """
    FACE_THRESHOLD = 25.0  # If more than 25% of frames have faces

    # Case 1: Faces detected → Deepfake model has the right context → trust it first
    if pct_faces_detected >= FACE_THRESHOLD:
        if avg_df_prob >= 0.50:
            return {
                "verdict": "manipulated",  # Maps to "Deepfake" in the extension
                "confidence": float(avg_df_prob),
                "explanation": "تم اكتشاف تلاعب في الوجوه (Deepfake).",
                "sources": []
            }
        elif avg_ai_prob >= 0.50:
            return {
                "verdict": "ai_generated",
                "confidence": float(avg_ai_prob),
                "explanation": "المحتوى مولد بالذكاء الاصطناعي.",
                "sources": []
            }

    # Case 2: No significant faces → AIGC model is the primary evidence
    else:
        if avg_ai_prob >= 0.50:
            return {
                "verdict": "ai_generated",
                "confidence": float(avg_ai_prob),
                "explanation": "المحتوى مولد بالذكاء الاصطناعي.",
                "sources": []
            }

    # Case 3: All signals are weak → Real
    p_real = (1.0 - avg_df_prob) * (1.0 - avg_ai_prob)
    return {
        "verdict": "real",
        "confidence": float(p_real),
        "explanation": "لم يتم اكتشاف تلاعب أو توليد بالذكاء الاصطناعي.",
        "sources": []
    }

# ─── 2. Route Handler ───
@router.post("/detect-media")
async def detect_media(request: DetectMediaRequest, req: Request):
    if not request.video_url:
        return {"verdict": "inconclusive", "confidence": 0.0, "explanation": "الصور غير مدعومة بعد.", "sources": []}

    target_url = request.video_url
    print(f"[HAQQ] Processing video URL: {target_url[:80]}...")

    yunet = req.app.state.yunet
    gend_model = req.app.state.gend_model
    aigc_pipeline = req.app.state.aigc_pipeline

    # 1. Download to Temp File
    fd, temp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(target_url, follow_redirects=True)
            resp.raise_for_status()
            with open(temp_path, "wb") as f:
                f.write(resp.content)

        # 2. Extract Frames with OpenCV
        cap = cv2.VideoCapture(temp_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames == 0:
            raise HTTPException(status_code=400, detail="Could not read video frames")
            
        n_frames = 8
        frame_indices = [int(i * (total_frames - 1) / (n_frames - 1)) for i in range(n_frames)]
        
        frames = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame_bgr = cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(frame_rgb))
        cap.release()

        # 3. Process Frames with YuNet
        processed_images = []
        faces_detected = []
        margin = 20

        for frame in frames:
            frame_cv = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
            h, w, _ = frame_cv.shape
            yunet.setInputSize((w, h))
            
            _, faces = yunet.detect(frame_cv)
            
            if faces is not None and len(faces) > 0:
                box = faces[0][:4].astype(int)
                x, y, bw, bh = box
                x1 = max(0, x - margin)
                y1 = max(0, y - margin)
                x2 = min(w, x + bw + margin)
                y2 = min(h, y + bh + margin)
                
                face_crop = frame.crop((x1, y1, x2, y2))
                processed_images.append(face_crop)
                faces_detected.append(True)
            else:
                processed_images.append(frame)
                faces_detected.append(False)

        # 4. GenD Inference (Only on Face Frames)
        deepfake_scores = []
        face_images = [img for img, has_face in zip(processed_images, faces_detected) if has_face]
        
        if face_images:
            tensors = [gend_model.feature_extractor.preprocess(img) for img in face_images]
            batch_tensor = torch.stack(tensors).to(gend_model.device)
            with torch.no_grad():
                logits = gend_model(batch_tensor)
                probs = torch.softmax(logits, dim=-1).cpu().numpy()
                deepfake_scores = [float(p[1]) for p in probs]
                
        avg_df_prob = float(np.mean(deepfake_scores)) if deepfake_scores else 0.0

        # 5. SigLIP Inference (On All Original Frames)
        aigc_scores = []
        for frame in frames:
            res = aigc_pipeline(frame)
            aigc_dict = {item['label'].lower(): item['score'] for item in res}
            aigc_scores.append(aigc_dict.get('ai', 0.0))
            
        avg_ai_prob = float(np.mean(aigc_scores)) if aigc_scores else 0.0

        # 6. Face-Aware Fusion
        pct_faces = (sum(faces_detected) / len(frames)) * 100.0
        result = face_aware_fusion(avg_df_prob, avg_ai_prob, pct_faces)
        
         # ---- print statements ----
        print("\n" + "="*50)
        print("[HAQQ MEDIA PIPELINE RESULTS]")
        print(f"Total Frames Analyzed: {len(frames)}")
        print(f"Frames with Faces:     {sum(faces_detected)} ({pct_faces:.1f}%)")
        print(f"Avg Deepfake Score:    {avg_df_prob:.3f} (GenD)")
        print(f"Avg AI-Gen Score:      {avg_ai_prob:.3f} (SigLIP)")
        print(f"FINAL VERDICT:         {result['verdict'].upper()} (Conf: {result['confidence']:.3f})")
        print("="*50 + "\n")
        # -----------------------------------------
        
        # Attach metadata for debugging
        result["metadata"] = {
            "avg_df_prob": avg_df_prob,
            "avg_ai_prob": avg_ai_prob,
            "faces_detected": sum(faces_detected)
        }
        return result

    finally:
        # 7. Cleanup: Delete Temp File Instantly
        # 7. Cleanup: Release OpenCV and Delete Temp File Instantly
        try:
            if 'cap' in locals() and cap is not None:
                cap.release()
        except Exception:
            pass
            
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass