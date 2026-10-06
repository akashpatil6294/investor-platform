# Use Windows' trusted root certificates for outbound HTTPS clients.
# This keeps TLS verification enabled while supporting the system CA store.
import truststore

truststore.inject_into_ssl()

from pathlib import Path
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from routes.chat import router as chat_router
from routes.ingest import router as ingest_router
from routes.health import router as health_router
from routes.notes import router as notes_router
from services import chat_runtime
from services.financial import enrich
from services.reports import source_path

from database.metrics import (
    get_metrics,
    get_metrics_by_company_year,
)


BASE_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(chat_runtime.warmup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    lifespan=lifespan,
    title="Investor Platform API",
    description="Financial data API for the Investor Platform",
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.strip() for s in os.getenv('CORS_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000').split(',') if s.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ============================================================
# FRONTEND (static assets + dashboard shell)
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "template" / "dashboard.html")


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return FileResponse(BASE_DIR / "template" / "dashboard.html")


# ============================================================
# HEALTH CHECK
# ============================================================

app.include_router(health_router, prefix='/api')
app.include_router(notes_router, prefix='/api')

@app.middleware('http')
async def request_guards(request, call_next):
    if request.method == 'POST':
        origin = request.headers.get('origin')
        allowed = [s.strip() for s in os.getenv('CORS_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000').split(',')]
        if origin and origin not in allowed:
            return JSONResponse({'code':'forbidden_origin','message':'This origin is not allowed.','retryable':False},status_code=403)
        maximum = 26*1024*1024 if request.url.path == '/api/ingest' else 160000
        try:
            if int(request.headers.get('content-length',0)) > maximum:
                return JSONResponse({'code':'request_too_large','message':'Request body is too large.','retryable':False},status_code=413)
        except ValueError:
            return JSONResponse({'code':'invalid_request','message':'Invalid content length.','retryable':False},status_code=400)
    response = await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    if request.url.path in ('/','/dashboard') or request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/source/{name}')
def source(name: str):
    path = source_path(name)
    if path is None:
        raise HTTPException(404, 'Source not found.')
    return FileResponse(path, media_type='application/pdf' if path.suffix.lower()=='.pdf' else 'text/plain')

@app.get('/api/insights/{company}/{year}')
def insights(company: str, year: int):
    from sqlalchemy import text
    from database.postgres_sql import get_engine
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text('SELECT payload,source,version FROM report_insights WHERE lower(company)=lower(:company) AND year=:year'), {'company':company,'year':year}).mappings().first()
            return dict(row) if row else {'payload':None}
    except Exception:
        return JSONResponse({'code':'insights_unavailable','message':'Evidence-backed insights have not been refreshed yet.','retryable':True},status_code=503)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'code': 'invalid_request', 'message': 'Invalid request. Questions must contain 1–1,000 characters; history is limited to 6 turns.', 'retryable': False}, status_code=422)


# ============================================================
# GET ALL FINANCIAL METRICS
# ============================================================

@app.get("/api/metrics")
def metrics():
    try:
        rows = get_metrics()

        return {
            "success": True,
            "count": len(rows),
            "data": enrich(rows),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Metrics database unavailable.",
        )


# ============================================================
# GET METRICS FOR COMPANY + YEAR
# ============================================================

@app.get("/api/metrics/{company}/{year}")
def company_metrics(company: str, year: int):
    try:
        result = get_metrics_by_company_year(
            company=company,
            year=year,
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"No financial metrics found for {company} {year}",
            )

        return {
            "success": True,
            "data": enrich([result])[0],
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Metrics database unavailable.",
        )


# ============================================================
# CHATBOT / RAG
# ============================================================

app.include_router(
    chat_router,
    prefix="/api",
)

app.include_router(
    ingest_router,
    prefix="/api",
)
