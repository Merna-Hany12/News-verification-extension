import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from backend.observability.axiom_logger import axiom_logger

class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate a unique ID for this request
        request_id = str(uuid.uuid4())
        
        # Attach to request state so route handlers can access it
        request.state.request_id = request_id
        
        start_time = time.time()
        
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            raise
        finally:
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Log general HTTP request metrics to Axiom
            axiom_logger.log_http_request({
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "latency_ms": elapsed_ms,
            })
            
        return response
