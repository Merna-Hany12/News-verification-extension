from dotenv import load_dotenv
import os

# Load environment variables from .env file before importing backend components
load_dotenv()

import uvicorn
from backend.api.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting HAQQ backend server on port {port}...")
    uvicorn.run("backend.api.app:app", host="0.0.0.0", port=port, reload=False)
