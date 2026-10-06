"""Startup warmup and dependency readiness without importing the KPI pipeline."""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
MODELS = [m.strip().removeprefix('models/') for m in os.getenv('GEMINI_CHAT_MODELS', os.getenv('GEMINI_CHAT_MODEL', 'gemini-2.5-flash,gemini-2.5-flash-lite')).strip('\"\'').split(',') if m.strip()]
state = {'postgres': False, 'vector_store': False, 'embedding': False, 'gemini': False, 'warming': True}
retriever = None
client = None
valid_models = []
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='research')

class Embeddings:
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(os.getenv('EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2'))
    def embed_query(self, text):
        return self.model.encode(text, normalize_embeddings=True).tolist()
    def embed_documents(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()

def check_database():
    from database.postgres_sql import get_engine
    with get_engine().connect() as connection:
        connection.exec_driver_sql('SELECT 1')

def warm_retriever():
    global retriever
    from vectorstore.supabase_vectorstore import SupabaseVectorStore, Retriever
    store = SupabaseVectorStore()
    store.client.table('documents').select('id').limit(1).execute()
    state['vector_store'] = True
    embeddings = Embeddings()
    embeddings.embed_query('Annual report revenue')
    state['embedding'] = True
    retriever = Retriever(store, embeddings)

def warm_gemini():
    global client, valid_models
    from google import genai
    from google.genai import types
    if not os.getenv('GEMINI_API_KEY'):
        raise RuntimeError('GEMINI_API_KEY is missing')
    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(timeout=15000))
    valid_models = []
    for model in MODELS:
        try:
            info = client.models.get(model=model)
            # Model lookup alone can succeed for a model that cannot generate.
            client.models.generate_content(model=model, contents='Reply OK.',
                config=types.GenerateContentConfig(max_output_tokens=256))
            valid_models.append(model)
        except Exception:
            log.error('A configured Gemini model is unavailable; check model name, key access and network.')
    if not valid_models: raise RuntimeError('No configured Gemini models could be validated')
    state['gemini'] = True

async def warmup():
    async def run(name, fn):
        try:
            await asyncio.to_thread(fn)
            if name == 'postgres': state[name] = True
        except Exception:
            state[name] = False
            log.error('Readiness failed for %s; check configuration and connectivity. Secrets omitted.', name)
    await asyncio.gather(run('postgres', check_database), run('vector_store', warm_retriever), run('gemini', warm_gemini))
    state['warming'] = False
    while True:
        await asyncio.sleep(30)
        jobs = [run('postgres', check_database)]
        if retriever is None: jobs.append(run('vector_store', warm_retriever))
        else:
            def ping_vector():
                retriever.vector_store.client.table('documents').select('id').limit(1).execute()
                state['vector_store'] = True
            jobs.append(run('vector_store', ping_vector))
        if not state['gemini']: jobs.append(run('gemini', warm_gemini))
        await asyncio.gather(*jobs)

def readiness():
    if state['warming']: label = 'Warming up'
    elif not state['postgres']: label = 'Database down'
    elif not state['gemini']: label = 'LLM unavailable'
    elif not state['vector_store'] or not state['embedding']: label = 'Retrieval unavailable'
    else: label = 'Ready'
    return {'status': label, 'ready': label == 'Ready', 'checks': dict(state)}
