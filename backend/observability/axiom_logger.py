import os
import asyncio
from typing import Any, Dict, List
import logging
import datetime
import urllib.parse
from typing import Optional

def extract_platform(url: Optional[str]) -> str:
    """Extract the social media platform from a URL for Axiom analytics."""
    if not url: return "unknown"
    domain = urllib.parse.urlparse(url).netloc.lower()
    
    # Check Instagram first because it frequently uses Facebook's CDN (fbcdn.net)
    if "instagram" in domain or "cdninstagram.com" in domain: return "instagram"
    
    if "facebook.com" in domain or "fb.watch" in domain or "fbcdn.net" in domain: return "facebook"
    if "tiktok" in domain or "tiktokcdn.com" in domain: return "tiktok"
    if "twitter.com" in domain or "x.com" in domain or "twimg.com" in domain: return "twitter"
    if "youtube.com" in domain or "youtu.be" in domain: return "youtube"
    return "other"

# Graceful degradation if axiom-py is not installed or configured
try:
    from axiom_py import Client
except ImportError:
    Client = None

class AxiomLogger:
    def __init__(self):
        self.token = os.environ.get("AXIOM_TOKEN")
        self.dataset = os.environ.get("AXIOM_DATASET", "haqq-events")
        
        self.client = None
        if self.token and Client:
            try:
                self.client = Client(token=self.token)
                print(f"[Observability] Axiom logging enabled for dataset: {self.dataset}")
            except Exception as e:
                logging.error(f"Failed to initialize Axiom client: {e}")
        else:
            print("[Observability] Axiom logging disabled (no token or missing axiom_py).")
            
        # Buffer for batching events
        self._buffer: List[Dict[str, Any]] = []
        self._batch_size = 50
        self._flush_interval_seconds = 5.0
        self._flush_task = None
        
    def _ensure_flush_task(self):
        """Ensure the background flush task is running on the current event loop."""
        if not self.client:
            return
            
        if self._flush_task is None or self._flush_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._flush_task = loop.create_task(self._periodic_flush())
            except RuntimeError:
                pass # No running event loop
            
    async def _periodic_flush(self):
        """Periodically flush the event buffer to Axiom."""
        while True:
            await asyncio.sleep(self._flush_interval_seconds)
            await self.flush()
            
    async def flush(self):
        """Flush current buffer to Axiom."""
        if not self.client or not self._buffer:
            return
            
        batch = self._buffer.copy()
        self._buffer.clear()
        
        try:
            # We use to_thread because Axiom's ingest is synchronous in the Python SDK
            await asyncio.to_thread(self.client.ingest_events, self.dataset, batch)
        except Exception as e:
            logging.error(f"Failed to ingest events to Axiom: {e}")
            
    def _enqueue_event(self, event: Dict[str, Any]):
        """Add an event to the buffer, adding a timestamp if missing."""
        if "_time" not in event:
            event["_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
        if self.client:
            self._buffer.append(event)
            self._ensure_flush_task()
            
            if len(self._buffer) >= self._batch_size:
                # Trigger a flush in the background
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.flush())
                except RuntimeError:
                    pass

    def log_verification_event(self, event: Dict[str, Any]):
        self._enqueue_event({**event, "event_type": "verification"})
        
    def log_media_detection_event(self, event: Dict[str, Any]):
        self._enqueue_event({**event, "event_type": "media_detection"})
        
    def log_http_request(self, event: Dict[str, Any]):
        self._enqueue_event({**event, "event_type": "http_request"})

# Global singleton
axiom_logger = AxiomLogger()
