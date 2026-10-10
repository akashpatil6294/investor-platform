from datetime import date
import pytest
from ingestion.units import normalize_currency, normalize_unit, unit_multiplier, parse_reported_number
from ingestion.statements import locate_statement_pages
from rag.kpi_extractor_v2 import deterministic_numeric_extraction, _StatementTable, derive_operating_income, validate_evidence
from rag.validation import extraction_status, run_checks
from database.save_metrics import build_record
from services.financial import numeric_answer

@pytest.mark.parametrize('unit,multiplier',[('crores',1e7),('lakh',1e5),('millions',1e6),('thousand',1e3)])
def test_units(unit,multiplier):
    assert unit_multiplier(unit)==multiplier

@pytest.mark.parametrize('token,value',[('(5.57)',-5.57),('₹ (1,234)',-1234),('204,933',204933),('31March2024',None)])
def test_numbers(token,value):
    assert parse_reported_number(token)==value

def test_year_cannot_substitute():
    table=_StatementTable([], '2024-03-31')
    assert table._pick_period([(1,10),(2,20)],date(2024,3,31),[date(2025,3,31),date(2023,3,31)])==(None,None)
    calls=[]
    assert numeric_answer('revenue',['Acme'],2024,lambda c,y:calls.append(y)) is None
    assert calls==[2024]

def test_no_invented_operating_income():
    assert derive_operating_income({'revenue':{'value':10},'_total_expenses':{'value':5}}) is None

def test_allnull_and_checks():
    assert build_record('Acme',2024,{},extraction_status='ok')['extraction_status']=='failed'
    assert extraction_status({},[])=='failed'
    assert any(x['check']=='accounting_identity' for x in run_checks({'total_assets':100,'total_liabilities':80,'total_equity':40}))
    assert extraction_status({'revenue':100},[{'check':'unit'}])=='needs_review'

def test_synonyms_and_basis():
    pages=[(1,'Standalone income statement\n|Year ended|2025|2024|\n|Revenue from operations|999|900|'),
           (2,'Consolidated income statement\n|Year ended|2025|2024|\n|Revenue from operations|100|80|\n|Operating income|20|10|')]
    locations=locate_statement_pages(pages)
    result=deterministic_numeric_extraction(locations,'2025-03-31')
    assert result['revenue']['value']==100
    assert result['operating_income']['value']==20
    assert result['revenue']['page']==2

def test_unsupported_citation_rejected():
    result=validate_evidence({'revenue':{'value':100,'page':2,'quote':'invented'}},[(2,'revenue 50')])
    assert result['revenue'].get('value') is None

def test_mixed_currency_no_ranking():
    rows={'One':{'revenue':10,'currency':'INR','unit':'crore'},'Two':{'revenue':5,'currency':'USD','unit':'million'}}
    result=numeric_answer('compare revenue',['One','Two'],2025,lambda c,y:rows[c])
    assert 'no ranking' in result

def test_text_and_scanned_pdf(tmp_path,monkeypatch):
    import fitz
    from ingestion.pdf_to_markdown import PDFToMarkdownConverter
    pdf=tmp_path/'arbitrary.pdf'
    doc=fitz.open();page=doc.new_page();page.insert_textbox(fitz.Rect(30,30,550,700),'Annual report '+('native text '*40));doc.new_page();doc.save(pdf);doc.close()
    calls=[]
    def ocr(page):
        calls.append(page.number)
        return '|Revenue from operations|100|80|'
    pages=PDFToMarkdownConverter().convert_pages(str(pdf),ocr_fn=ocr)
    assert calls==[1]
    assert pages[1][0]==2 and 'Revenue' in pages[1][1]

def test_persistent_review_does_not_extract(monkeypatch,tmp_path):
    from services import pipeline
    from database import ingest_jobs
    job={'status':'queued','filename':'arbitrary.pdf','detection':None}
    updates=[]
    monkeypatch.setattr(ingest_jobs,'get_job',lambda _:job)
    monkeypatch.setattr(ingest_jobs,'update_job',lambda _id,data,**kw:updates.append(data))
    monkeypatch.setattr(pipeline,'_convert_and_ocr',lambda *a:'report')
    monkeypatch.setattr(pipeline,'_detect',lambda *a:{'company':'Acme','year':2025})
    monkeypatch.setattr(pipeline,'_store_chunks',lambda *a:pytest.fail('extracted before confirmation'))
    pipeline.run_ingestion_job('test')
    assert updates[-1]['status']=='awaiting_review'
    assert updates[-1]['detection']['company']=='Acme'
