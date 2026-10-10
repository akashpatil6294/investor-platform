from fastapi import APIRouter
from fastapi.responses import JSONResponse
from services.chat_runtime import readiness

router = APIRouter(tags=['health'])

@router.get('/health')
def health():
    return {'success': True, 'status': 'healthy'}

@router.get('/ready')
def ready():
    result = readiness()
    return JSONResponse(result, status_code=200 if result['ready'] else 503)


@router.get("/healthz")
def healthz():
    """Liveness probe: always 200 regardless of dependency state."""
    return {"ok": True}
