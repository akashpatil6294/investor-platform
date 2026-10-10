# Use Windows' trusted root certificates for outbound HTTPS clients.
# This keeps TLS verification enabled while supporting the system CA store.
import truststore

truststore.inject_into_ssl()

from pathlib import Path
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from routes.chat import router as chat_router
from routes.ingest import router as ingest_router
from routes.health import router as health_router
from routes.notes import router as notes_router
from routes.metrics_admin import router as metrics_admin_router
from services import chat_runtime
from services.financial import enrich
from services.reports import source_path

from database.metrics import (
    get_company_catalog,
    get_metrics,
    get_metrics_by_company_year,
)


BASE_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app):
    from database.migrations import run_migrations
    from database.ingest_jobs import recover_interrupted

    def _prepare():
        run_migrations()
        recovered = recover_interrupted()
        if recovered:
            logging.getLogger(__name__).warning("Recovered %d interrupted ingestion job(s).", recovered)
    try:
        await asyncio.to_thread(_prepare)
    except Exception:
        logging.getLogger(__name__).error("Startup migration failed; check database connectivity.")
    task = asyncio.create_task(chat_runtime.warmup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
for _name in (
    'services.chat_runtime',
    'database.migrations',
    'database.postgres_sql',
    'vectorstore.supabase_vectorstore',
):
    logging.getLogger(_name).setLevel(logging.INFO)
import os as _os
import time as _time
_boot_t0 = _time.time()
try:
    print(f"[BOOT] importing sentence_transformers...", flush=True)
    from sentence_transformers import SentenceTransformer as _BootstrapST
    print(f"[BOOT]   import OK in {_time.time()-_boot_t0:.2f}s", flush=True)
    _boot_t1 = _time.time()
    _BOOT_MODEL = _BootstrapST(
        _os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )
    print(f"[BOOT]   model loaded in {_time.time()-_boot_t1:.2f}s", flush=True)
except Exception as _e:
    print(f"[BOOT]   FAILED: {_e!r}", flush=True)
    _BOOT_MODEL = None
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
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
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
app.include_router(metrics_admin_router, prefix='/api')

@app.middleware('http')
async def request_guards(request, call_next):
    if request.method in ('POST', 'PATCH', 'DELETE'):
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
    if path and path.suffix.lower() == '.md':
        pdf = BASE_DIR / 'data' / 'raw_pdfs' / (path.stem + '.pdf')
        if pdf.is_file(): path = pdf
    if path is None:
        raise HTTPException(404, 'Source not found.')
    return FileResponse(path, media_type='application/pdf' if path.suffix.lower()=='.pdf' else 'text/plain')

@app.get('/api/insights/{company}/{year}')
def insights(company: str, year: int):
    from sqlalchemy import text
    from database.postgres_sql import get_engine
    try:
        metric_row = get_metrics_by_company_year(company, year)
        if metric_row and metric_row.get('source_file'):
            import json
            payload = {}
            for key in ('risk_factors', 'growth_drivers'):
                items = metric_row.get(key)
                if isinstance(items, str):
                    try: items = json.loads(items)
                    except ValueError: items = []
                payload[key] = items if isinstance(items, list) else []
            if any(any(isinstance(i, dict) and i.get("page") for i in items) for items in payload.values()):
                return {'payload':payload, 'source':metric_row['source_file'], 'version':metric_row.get('updated_at')}
        with get_engine().connect() as conn:
            row = conn.execute(text('SELECT payload,source,version FROM report_insights WHERE lower(company)=lower(:company) AND year=:year'), {'company':company,'year':year}).mappings().first()
            return dict(row) if row else {'payload':None}
    except Exception:
        return JSONResponse({'code':'insights_unavailable','message':'Evidence-backed insights have not been refreshed yet.','retryable':True},status_code=503)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'code': 'invalid_request', 'message': 'Invalid request. Questions must contain 1â€“1,000 characters; history is limited to 6 turns.', 'retryable': False}, status_code=422)


# ============================================================
# COMPANY CATALOG (drives sidebar, year selector, comparisons)
# ============================================================

@app.get("/api/companies")
def companies():
    try:
        return {"success": True, "count": len(get_company_catalog()),
                "companies": get_company_catalog()}
    except Exception:
        raise HTTPException(status_code=500, detail="Metrics database unavailable.")


class AliasesPayload(BaseModel):
    company: str = Field(min_length=1, max_length=120)
    year: int | None = Field(default=None, ge=1900, le=2100)
    period_end: str | None = Field(default=None, max_length=10)
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=16)
    basis: str | None = Field(default=None, max_length=16)
    aliases: list[str] = Field(default_factory=list, max_length=20)


@app.post("/api/aliases")
def save_aliases(payload: AliasesPayload):
    """Upsert the alias rows for a company (edited from the dashboard)."""
    from database.aliases import upsert_alias

    company = payload.company.strip()
    if not company:
        raise HTTPException(422, "Company name is required.")
    saved, failed = [], []
    for alias in payload.aliases:
        alias = (alias or "").strip()
        if not alias or alias.casefold() == company.casefold():
            continue
        try:
            upsert_alias(alias, company)
            saved.append(alias)
        except Exception:
            failed.append(alias)
    if failed and not saved:
        raise HTTPException(503, "Could not save the alias table; retry once the database is reachable.")
    return {"success": True, "company": company, "saved": saved, "failed": failed}


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
