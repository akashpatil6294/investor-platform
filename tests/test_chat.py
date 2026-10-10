import pytest
from fastapi.testclient import TestClient
from api.main import app
from routes import chat
from services import chat_runtime as runtime
from services.financial import money, ratios, intent, numeric_answer
from services.query import _expand_short_query

def test_expand():
    assert 'net income' in _expand_short_query('profit', 'Apple', 2024)
    assert _expand_short_query('Explain the competitive landscape', 'Apple', 2024) == 'Explain the competitive landscape'

def test_format_and_math():
    assert money(637959) == '$637,959M ($638.0B)'
    assert money(None) == 'Not reported'
    result = ratios({'revenue':100,'operating_income':25,'net_income':20,'cash_flow':30,'total_assets':80,'total_liabilities':40})
    assert result == {'operating_margin':.25,'net_margin':.2,'liabilities_to_assets':.5,'cash_conversion':1.5}
    assert ratios({'revenue':0})['operating_margin'] is None

@pytest.mark.parametrize('question,expected', [('revenue','numeric'),('why did revenue rise','narrative'),('Apple vs Microsoft operating margin','comparison'),('revenue YoY','yoy')])
def test_intent(question, expected):
    assert intent(question)[0] == expected

def test_exact_scope():
    assert chat._matches_company_year({'metadata':{'company':'Apple','year':2024}}, 'Apple',2024)
    assert not chat._matches_company_year({'content':'Apple 2024','metadata':{'company':'Tesla','year':2024}},'Apple',2024)
    assert not chat._matches_company_year({'metadata':{'company':'Apple','year':2023}},'Apple',2024)
    assert not chat._matches_company_year({'content':'Apple 2024'},'Apple',2024)

def test_no_match_never_leaks(monkeypatch):
    class Retriever:
        def retrieve(self, *args, **kwargs):
            assert kwargs['company']=='Apple' and kwargs['year']==2024
            return [{'content':'Tesla secret', 'company':'Tesla','year':2024}]
    monkeypatch.setattr(runtime,'retriever',Retriever())
    assert chat.sources_for('risk',['Apple'],2024)==[]

def test_chat_mocked_gemini_and_retriever(monkeypatch):
    monkeypatch.setattr(chat, "get_metrics", lambda: [])
    class Retriever:
        def retrieve(self,*a,**kw):return [{'content':'Supplier concentration is a risk.', 'company':'Apple','year':2024,'metadata':{'source':'2024_Apple.md','page':12}}]
    class Models:
        def generate_content_stream(self,**kw):
            assert 'Supplier concentration' in kw['contents']
            yield type('Chunk',(),{'text':'Supplier concentration is a risk [1].'})()
    monkeypatch.setattr(runtime,'retriever',Retriever())
    monkeypatch.setattr(runtime,'client',type('Client',(),{'models':Models()})())
    monkeypatch.setattr(runtime,'valid_models',['test-model'])
    chat._hits.clear()
    response=TestClient(app).post('/api/chat',json={'question':'What are the risks?','company':'Apple','year':2024})
    assert response.status_code==200
    assert response.json()['sources'][0]['page']==12
    assert response.json()['model_used']=='test-model'

def test_limits():
    response=TestClient(app).post('/api/chat',json={'question':'x'*1001})
    assert response.status_code==422 and response.json()['code']=='invalid_request'

def test_comparison_and_yoy():
    rows={('Apple',2024):{'revenue':100,'operating_income':30},('Microsoft',2024):{'revenue':200,'operating_income':80},('Apple',2023):{'revenue':80}}
    for row in rows.values(): row.update(currency='USD',unit='million',basis='consolidated')
    lookup=lambda c,y:rows.get((c,y))
    assert '10.00 percentage points below' in numeric_answer('Apple vs Microsoft operating margin',['Apple','Microsoft'],2024,lookup)
    assert '+25.00%' in numeric_answer('revenue YoY',['Apple'],2024,lookup)
