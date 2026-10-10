"""Separate, opt-in page-aware reindex + versioned qualitative extraction.
Run: python -m scripts.refresh_report_evidence [--apply] [--company Apple]
Default is a read-only inventory. --apply updates document chunks transactionally and
writes validated insights; numeric extraction/persistence is not changed.
"""
import argparse
import json
import re
import time
import truststore
truststore.inject_into_ssl()
import fitz
from sqlalchemy import text
from database.postgres_sql import get_engine
from services.reports import REPORTS, ROOT
from services.chat_runtime import Embeddings, warm_gemini
from services import chat_runtime as runtime

def pages_for(path):
    with fitz.open(path) as pdf:
        return [page.get_text(sort=True) for page in pdf]

def chunks_for(pages, company, year, filename):
    for page, content in enumerate(pages, 1):
        lines = [s.strip() for s in content.splitlines() if s.strip()]
        heading = next((s for s in lines[:12] if 4 < len(s) < 100 and not s.isdigit()), None)
        for start in range(0, len(content), 1600):
            chunk = content[start:start+1800].strip()
            if chunk:
                yield {'content':chunk, 'metadata':{'company':company,'year':year,'page':page,'section':heading,'source':filename}}

def validate_insights(data, pages):
    from services.financial import normalize_money_text
    for field in ('growth_drivers','risk_factors'):
        if not isinstance(data.get(field),list): raise ValueError('Missing insight array')
        verified=[]
        for item in data[field]:
            if not all(isinstance(item.get(k),str) and item[k].strip() for k in ('title','detail','evidence_quote')): raise ValueError('Invalid insight')
            page = item.get('evidence_page')
            if not isinstance(page,int) or not 1 <= page <= len(pages): raise ValueError('Invalid page')
            normalize = lambda s: re.sub(r'\s+',' ',s).strip().casefold()
            if normalize(item['evidence_quote']) not in normalize(pages[page-1]): continue
            item['detail']=normalize_money_text(item['detail'])
            verified.append(item)
        data[field]=verified
        if not verified: raise ValueError('No verified evidence remained for '+field)
    return data

def extract(pages, company, year):
    from google.genai import types
    prompt = (ROOT/'prompts/qualitative.v2.txt').read_text(encoding='utf-8')
    content = f'{company} FY{year}\n'+'\n'.join(f'PDF PAGE {i}\n{page}' for i,page in enumerate(pages,1))
    for model in runtime.valid_models:
        for attempt in range(3):
            try:
                response = runtime.client.models.generate_content(model=model,contents=prompt+'\n'+content,
                    config=types.GenerateContentConfig(response_mime_type='application/json', max_output_tokens=6000))
                return validate_insights(json.loads(response.text),pages)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, (ValueError, json.JSONDecodeError)) else f'provider status {getattr(exc,"code", "unavailable")}'
                print(f'{company}: attempt {attempt+1}: {type(exc).__name__}; {reason[:100]}', flush=True)
                if attempt < 2: time.sleep(2**attempt)
    raise RuntimeError('Insight generation or evidence validation failed')

def extract_groq(pages, company, year):
    """Use the existing ingestion provider with a bounded, page-labelled context."""
    import os
    from groq import Groq
    prompt=(ROOT/'prompts/qualitative.v2.txt').read_text(encoding='utf-8')
    preferred={'Apple':[5,6,9,10,11,31], 'Microsoft':[3,11,13,18,19,53],
               'Tesla':[3,4,5,6,7,43], 'Amazon':[19,22,36,37,38,39]}
    excerpts=[]
    for i in preferred.get(company,range(1,len(pages)+1)):
        if i<=len(pages):excerpts.append(f'PDF PAGE {i}\n'+pages[i-1][:(1200 if company=='Tesla' else 1800)])
    client=Groq(api_key=os.environ['GROQ_API_KEY'],timeout=45,max_retries=2)
    response=client.chat.completions.create(model=os.getenv('GROQ_MODEL','openai/gpt-oss-20b'),
        messages=[{'role':'system','content':prompt},{'role':'user','content':f'{company} FY{year}\n'+'\n'.join(excerpts)}],
        response_format={'type':'json_object'},max_completion_tokens=2500,temperature=0.1)
    return validate_insights(json.loads(response.choices[0].message.content),pages)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--company');parser.add_argument('--index-only',action='store_true');parser.add_argument('--insights-only',action='store_true');parser.add_argument('--provider',choices=['gemini','groq'],default='gemini')
    args=parser.parse_args(); embeddings=None
    for filename,(company,year) in REPORTS.items():
        if year != 2024: continue
        if args.company and args.company.casefold()!=company.casefold(): continue
        pages=pages_for(ROOT/'data/raw_pdfs'/filename)
        chunks=list(chunks_for(pages,company,year,filename))
        print(f'{company} FY{year}: {len(pages)} pages, {len(chunks)} proposed chunks',flush=True)
        if not args.apply: continue
        if embeddings is None and not args.insights_only:
            embeddings=Embeddings()
        if not args.index_only and args.provider=='gemini' and runtime.client is None:
            warm_gemini()
            from google import genai
            from google.genai import types
            import os
            runtime.client = genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(timeout=60000))
        try:
            insights=None if args.index_only else (extract_groq(pages,company,year) if args.provider=='groq' else extract(pages,company,year))
            if args.insights_only: chunks=[]
            vectors=embeddings.embed_documents([c['content'] for c in chunks]) if chunks else []
            with get_engine().begin() as conn:
                conn.execute(text('''CREATE TABLE IF NOT EXISTS report_insights (
                    company text NOT NULL, year integer NOT NULL, version text NOT NULL,
                    source text NOT NULL, payload jsonb NOT NULL, PRIMARY KEY(company,year))'''))
                if chunks:
                    conn.execute(text('DELETE FROM documents WHERE lower(company)=lower(:company) AND year::text=:year'),{'company':company,'year':str(year)})
                for index,(chunk,vector) in enumerate(zip(chunks,vectors)):
                    chunk['metadata']['chunk_index']=index
                    conn.execute(text('''INSERT INTO documents (content,metadata,embedding,company,year,source_file)
                        VALUES (:content,CAST(:metadata AS jsonb),CAST(:embedding AS vector),:company,:year,:source)'''),
                        {'content':chunk['content'],'metadata':json.dumps(chunk['metadata']),'embedding':json.dumps(vector),
                         'company':company,'year':str(year),'source':filename})
                if insights is not None:
                    conn.execute(text('''INSERT INTO report_insights VALUES (:company,:year,'qualitative.v2',:source,CAST(:payload AS jsonb))
                        ON CONFLICT(company,year) DO UPDATE SET payload=excluded.payload,source=excluded.source,version=excluded.version'''),
                        {'company':company,'year':year,'source':filename,'payload':json.dumps(insights)})
            print(f'{company}: committed {len(chunks)} page-aware chunks'+(' and validated insights' if insights is not None else ''),flush=True)
        except Exception as exc:
            reason=str(exc)[:120] if isinstance(exc,ValueError) else 'provider or database unavailable'
            print(f'{company}: FAILED ({type(exc).__name__}; {reason}); no partial transaction committed',flush=True)

if __name__=='__main__': main()
