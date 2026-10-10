"""Startup warmup and dependency readiness without importing the KPI pipeline."""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

MODELS = [
    m.strip().removeprefix('models/')
    for m in os.getenv(
        'GEMINI_CHAT_MODELS',
        os.getenv('GEMINI_CHAT_MODEL', 'gemini-2.5-flash,gemini-2.5-flash-lite'),
    ).strip('"\'').split(',')
    if m.strip()
]

state = {
    'postgres': False,
    'vector_store': False,
    'embedding': False,
    'gemini': False,
    'warming': True,
}

retriever = None
client = None
valid_models = []
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='research')

# Singleton so the model is only constructed once, ever.
_embeddings_singleton = None


class Embeddings:
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(
            os.getenv('EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')
        )

    def embed_query(self, text):
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_documents(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def get_embeddings():
    """Return the shared Embeddings instance, constructing it on first call.

    Prefers the model already loaded at boot time by api.main (main thread,
    before uvicorn / ingestion threads start) — this avoids GIL starvation
    from the ingestion pipeline blocking torch init.
    """
    global _embeddings_singleton
    if _embeddings_singleton is not None:
        return _embeddings_singleton

    # Try to reuse the boot-time model from api.main.
    try:
        import api.main as _api_main
        boot_model = getattr(_api_main, "_BOOT_MODEL", None)
        if boot_model is not None:
            instance = Embeddings.__new__(Embeddings)
            instance.model = boot_model
            _embeddings_singleton = instance
            log.info('warm_embeddings: reused boot-time model')
            return _embeddings_singleton
        else:
            log.warning('warm_embeddings: boot model was None; loading fresh')
    except Exception as exc:
        log.warning('warm_embeddings: could not reuse boot model (%s); loading fresh', exc)

    _embeddings_singleton = Embeddings()
    return _embeddings_singleton

def check_database():
    from database.postgres_sql import get_engine
    with get_engine().connect() as connection:
        connection.exec_driver_sql('SELECT 1')
    state['postgres'] = True


def warm_vector_store():
    """Ping Supabase and return a reusable store handle."""
    from vectorstore.supabase_vectorstore import SupabaseVectorStore
    store = SupabaseVectorStore()
    store.client.table('documents').select('id').limit(1).execute()
    state['vector_store'] = True
    return store


def warm_embeddings(store=None):
    """Load the sentence-transformer model and build the retriever.

    Must be called from the MAIN thread (not via asyncio.to_thread).
    """
    global retriever
    log.info('warm_embeddings: begin')
    try:
        from vectorstore.supabase_vectorstore import SupabaseVectorStore, Retriever
        log.info('warm_embeddings: imports OK')

        embeddings = get_embeddings()
        log.info('warm_embeddings: SentenceTransformer loaded')

        embeddings.embed_query('Annual report revenue')
        log.info('warm_embeddings: probe embed OK')

        state['embedding'] = True

        if store is None:
            store = SupabaseVectorStore()
            log.info('warm_embeddings: fallback store created')

        retriever = Retriever(store, embeddings)
        log.info('warm_embeddings: retriever built; done')
        return embeddings
    except Exception:
        log.exception('warm_embeddings: FAILED')
        state['embedding'] = False
        raise


def warm_gemini():
    """Validate Gemini API key and configured model names."""
    global client, valid_models
    from google import genai
    from google.genai import types

    if not os.getenv('GEMINI_API_KEY'):
        raise RuntimeError('GEMINI_API_KEY is missing')

    client = genai.Client(
        api_key=os.environ['GEMINI_API_KEY'],
        http_options=types.HttpOptions(timeout=15000),
    )

    try:
        available = {
            m.name.removeprefix('models/')
            for m in client.models.list()
        }
    except Exception as exc:
        log.warning('Gemini models.list() failed: %s: %s', type(exc).__name__, exc)
        raise

    valid_models = [m for m in MODELS if m in available]
    missing = [m for m in MODELS if m not in available]
    if missing:
        log.warning('Gemini models not available on this key: %s', missing)

    if not valid_models:
        raise RuntimeError(
            f'None of the configured models are available: {MODELS}'
        )

    log.info('Gemini models validated: %s', valid_models)
    state['gemini'] = True


async def warmup():
    async def run(name, fn, *args):
        try:
            await asyncio.to_thread(fn, *args)
        except Exception:
            log.exception(
                'Readiness failed for %s; check configuration and connectivity.',
                name,
            )

    # Phase 1: postgres, vector store, gemini - in parallel.
    await asyncio.gather(
        run('postgres', check_database),
        run('vector_store', warm_vector_store),
        run('gemini', warm_gemini),
    )

    state['warming'] = False
    log.info('Phase 1 finished. State=%s', state)

    # Phase 2: embeddings MUST run on the main thread.
    # asyncio.to_thread would hang on Windows (torch DLL init in worker thread).
    if state['vector_store'] and not state['embedding']:
        try:
            from vectorstore.supabase_vectorstore import SupabaseVectorStore
            store = SupabaseVectorStore()
            warm_embeddings(store)   # <-- direct call, not to_thread
        except Exception:
            state['embedding'] = False
            log.exception('Readiness failed for embedding; check model access and cache.')
    else:
        log.warning(
            'Skipping embedding warmup: vector_store=%s embedding=%s',
            state['vector_store'], state['embedding'],
        )

    log.info('Warmup finished. State=%s', state)

    # Periodic re-checks - retry anything that failed.
    # After the first successful load, embeddings are safe to run in a thread.
    while True:
        await asyncio.sleep(30)

        jobs = [run('postgres', check_database)]

        if retriever is None:
            jobs.append(run('vector_store', warm_vector_store))
        else:
            def ping_vector():
                retriever.vector_store.client.table('documents').select('id').limit(1).execute()
                state['vector_store'] = True
            jobs.append(run('vector_store', ping_vector))

        if not state['embedding']:
            # Subsequent loads reuse the singleton; safe in a thread.
            jobs.append(run('embedding', warm_embeddings))

        if not state['gemini']:
            jobs.append(run('gemini', warm_gemini))

        await asyncio.gather(*jobs)


def readiness():
    if state['warming']:
        label = 'Warming up'
    elif not state['postgres']:
        label = 'Database down'
    elif not state['gemini']:
        label = 'LLM unavailable'
    elif not state['vector_store'] or not state['embedding']:
        label = 'Retrieval unavailable'
    else:
        label = 'Ready'

    return {
        'status': label,
        'ready': label == 'Ready',
        'checks': dict(state),
    }
