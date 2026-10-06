"""Import verified FY2023 financial statement rows, with PDF page provenance.
This intentionally does not run or change the existing ingestion pipeline.
"""
import argparse
import truststore
truststore.inject_into_ssl()
import json
import re
import fitz
from sqlalchemy import text
from database.postgres_sql import get_engine
from services.reports import ROOT
from services.chat_runtime import Embeddings

# Values were checked against the stated fiscal-year columns. Amazon reports
# oldest-to-newest; the others report newest-to-oldest. Microsoft FY2023 is the
# comparative column of its FY2024 filing, explicitly recorded below.
RECORDS={
 'Apple':('2023_Apple.pdf', {'revenue':(383285,31),'net_income':(96995,31),'operating_income':(114301,31),'cash_flow':(110543,35),'total_assets':(352583,33),'total_liabilities':(290437,33)}),
 'Amazon':('2023_Amazon.pdf', {'revenue':(574785,50),'net_income':(30425,50),'operating_income':(36852,50),'cash_flow':(84946,49),'total_assets':(527854,52),'total_liabilities':(325979,52)}),
 'Tesla':('2023_Tesla.pdf', {'revenue':(96773,51),'net_income':(14974,51),'operating_income':(8891,51),'cash_flow':(13256,54),'total_assets':(106618,50),'total_liabilities':(43009,50)}),
 'Microsoft':('2024_Microsoft.pdf', {'revenue':(211915,24),'net_income':(72361,24),'operating_income':(88523,24),'cash_flow':(87582,27),'total_assets':(411976,26),'total_liabilities':(205753,26)}),
}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 embeddings=Embeddings() if args.apply else None
 provenance={}
 for company,(filename,fields) in RECORDS.items():
  with fitz.open(ROOT/'data/raw_pdfs'/filename) as doc:
   pages={page:doc[page-1].get_text(sort=True) for _,page in fields.values()}
  for field,(value,page) in fields.items():
   if company=='Amazon' and field=='total_liabilities':
    assert '527,854' in pages[page] and '201,875' in pages[page] and value==527854-201875
   else:assert f'{value:,}' in pages[page], (company,field,'Evidence missing')
  provenance[company]={field:{'document':filename,'page':page,'value':value,'derived':company=='Amazon' and field=='total_liabilities'} for field,(value,page) in fields.items()}
  print(f'{company}: six FY2023 values verified; source {filename}',flush=True)
  if not args.apply:continue
  vectors=embeddings.embed_documents(list(pages.values()))
  with get_engine().begin() as conn:
   exists=conn.execute(text("SELECT id FROM financial_metrics WHERE lower(company)=lower(:company) AND year::text='2023' LIMIT 1"),{'company':company}).first()
   if not exists:
    values={field:str(v) for field,(v,_) in fields.items()}
    conn.execute(text('''INSERT INTO financial_metrics (company,year,revenue,net_income,operating_income,cash_flow_from_operations,total_assets,total_liabilities)
      VALUES (:company,'2023',:revenue,:net_income,:operating_income,:cash_flow,:total_assets,:total_liabilities)'''),{'company':company,**values})
   conn.execute(text("DELETE FROM documents WHERE lower(company)=lower(:company) AND year::text='2023' AND metadata->>'import_version'='verified-prior-v1'"),{'company':company})
   for (page,content),vector in zip(pages.items(),vectors):
    meta={'company':company,'year':2023,'source':filename,'page':page,'section':'Financial statements (FY2023 column)','import_version':'verified-prior-v1'}
    conn.execute(text('''INSERT INTO documents (content,metadata,embedding,company,year,source_file) VALUES
      (:content,CAST(:meta AS jsonb),CAST(:embedding AS vector),:company,'2023',:source)'''),
      {'content':content,'meta':json.dumps(meta),'embedding':json.dumps(vector),'company':company,'source':filename})
  print(f'{company}: prior-year metrics and statement evidence committed',flush=True)
 if args.apply:(ROOT/'config/prior_year_sources.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')

if __name__=='__main__':main()
