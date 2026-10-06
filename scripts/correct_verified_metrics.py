"""Narrow, auditable correction verified against Tesla FY2024 PDF page 10.
Default prints the proposed correction; --apply records the old value before update.
"""
import argparse
import re
from sqlalchemy import text
from database.postgres_sql import get_engine
from services.reports import ROOT
import fitz

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    changes=[('Tesla','revenue','revenue',97690,'2024_Tesla.pdf',10,r'Total revenues\s+97,690\s+96,773'),
             ('Microsoft','cash_flow','cash_flow_from_operations',118548,'2024_Microsoft.pdf',27,r'Net cash from operations\s+118,548\s+87,582')]
    for company,field,column,value,source,page_no,pattern in changes:
        with fitz.open(ROOT/'data/raw_pdfs'/source) as doc: page=doc[page_no-1].get_text(sort=True)
        if not re.search(pattern,page): raise RuntimeError('Expected evidence missing; no correction applied')
        print(f'{company} FY2024 {field}: verified ${value:,}M on PDF page {page_no}.')
        if not args.apply:continue
        with get_engine().begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS metric_corrections (
                company text, year text, field text, old_value text, new_value text,
                source text, page integer, applied_at timestamptz DEFAULT now())"""))
            row=conn.execute(text(f"SELECT id,{column} AS value FROM financial_metrics WHERE lower(company)=lower(:company) AND year::text='2024' ORDER BY created_at DESC LIMIT 1"),{'company':company}).mappings().first()
            if row and float(row['value'])!=value:
                conn.execute(text("INSERT INTO metric_corrections (company,year,field,old_value,new_value,source,page) VALUES (:company,'2024',:field,:old,:new,:source,:page)"),{'company':company,'field':field,'old':str(row['value']),'new':str(value),'source':source,'page':page_no})
                conn.execute(text(f'UPDATE financial_metrics SET {column}=:value WHERE id=:id'),{'value':str(value),'id':row['id']})
                print('Correction committed; previous value retained in metric_corrections.')
            else:print('No correction needed.')

if __name__=='__main__':main()
