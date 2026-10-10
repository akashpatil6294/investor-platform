import pytest
from routes import chat
from services import web_research

@pytest.fixture
def setup(monkeypatch):
    row={'company':'Acme','year':2025,'currency':'INR','unit':'million','basis':'consolidated',
         'period_end':'2025-03-31','revenue':100,'extraction_status':'partial',
         'metric_sources':{'revenue':{'document':'acme.md','page':2,'quote':'Revenue 100'}}}
    monkeypatch.setattr(chat,'get_metrics',lambda:[row])
    monkeypatch.setattr(chat,'get_metrics_by_company_year',lambda c,y:row if c=='Acme' and y==2025 else None)
    monkeypatch.setattr(chat,'sources_for',lambda *a:[])
    monkeypatch.setattr(web_research,'research',lambda *a:{'status':'not_found','sources':[]})
    return row,chat.ChatRequest(question='What was revenue?',company='Acme',year=2025)

def test_stored_tier(setup):
    row,req=setup
    result=chat.answer(req)
    assert result['answer_source']=='Stored metrics'
    assert '100' in result['answer'] and result['sources'][0]['page']==2

def test_report_tier_null_database(setup,monkeypatch):
    row,req=setup;row['revenue']=None
    monkeypatch.setattr(chat,'sources_for',lambda *a:[{'document':'acme.md','page':3,'content':'Revenue 200 INR million'}])
    monkeypatch.setattr(chat,'generate',lambda *a:('Revenue was INR 200 million [1].','mock'))
    result=chat.answer(req)
    assert result['answer_source']=='Report' and '200' in result['answer']

def test_web_tier(setup,monkeypatch):
    row,req=setup;row['revenue']=None
    monkeypatch.setattr(web_research,'research',lambda *a:{'status':'found','answer':'Not in the uploaded report. From the web: INR 300 million',
        'sources':[{'title':'Filing','url':'https://example.com/filing','retrieved_at':'2026-10-07'}]})
    result=chat.answer(req)
    assert result['answer_source']=='Web' and result['sources'][0]['url'].startswith('https:')

def test_honest_failure(setup):
    row,req=setup;row['revenue']=None
    result=chat.answer(req)
    assert [x['tier'] for x in result['checked_tiers']]==['Stored metrics','Report','Web']
    assert 'could not verify' in result['answer']
    assert 'Review' in result['answer']

def test_explicit_year_never_uses_adjacent(setup):
    row,req=setup;req.question='Revenue in 2024?'
    result=chat.answer(req)
    assert result['year']==2024 and result['answer_source'] is None
    assert '100' not in result['answer']
