from pydantic import BaseModel
from typing import Optional

class TextRequest(BaseModel):
    text: str

class ImageRequest(BaseModel):
    image_url: str

class VerifyRequest(BaseModel):
    text: str
    lang: str = "ar"   # "ar" or "en" — extension sends this

class VerifyContentRequest(BaseModel):
    text: Optional[str] = None
    image_url: Optional[str] = None
    lang: str = "ar"

class DetectMediaRequest(BaseModel):
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    post_permalink: Optional[str] = None
    extracted_frames: Optional[list[str]] = None