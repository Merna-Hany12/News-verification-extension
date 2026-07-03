from pydantic import BaseModel

class TextRequest(BaseModel):
    text: str

class ImageRequest(BaseModel):
    image_url: str

class VerifyRequest(BaseModel):
    text: str
    lang: str = "ar"   # "ar" or "en" — extension sends this
