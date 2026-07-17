from pydantic import BaseModel
from typing import Optional

class TextRequest(BaseModel):
    text: str

class ImageRequest(BaseModel):
    image_url: str

class VerifyRequest(BaseModel):
    text: str
    lang: str = "ar"   # "ar" or "en" — extension sends this

class DetectMediaRequest(BaseModel):
    image_url: Optional[str] = None
    video_url: Optional[str] = None