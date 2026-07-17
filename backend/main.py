import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from the project root .env file before importing backend components
load_dotenv(PROJECT_ROOT / ".env")

import uvicorn
from backend.api.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting HAQQ backend server on port {port}...")
    uvicorn.run("backend.api.app:app", host="0.0.0.0", port=port, reload=False)
