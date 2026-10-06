"""15 live endpoint checks against verified filing figures. Exit nonzero on failure."""
import argparse
import sys
import requests

CASES=[
 ('Apple revenue', 'Apple',2024,'$391,035M'),
 ('Amazon revenue','Amazon',2024,'$637,959M'),
 ('Microsoft revenue','Microsoft',2024,'$245,122M'),
 ('Tesla revenue','Tesla',2024,'$97,690M'),
 ('Apple net income','Apple',2024,'$93,736M'),
 ('Amazon net income','Amazon',2024,'$59,248M'),
 ('Microsoft net income','Microsoft',2024,'$88,136M'),
 ('Tesla net income','Tesla',2024,'$7,153M'),
 ('Apple total assets','Apple',2024,'$364,980M'),
 ('Microsoft total assets','Microsoft',2024,'$512,163M'),
 ('Tesla total assets','Tesla',2024,'$122,070M'),
 ('Apple revenue YoY','Apple',2024,'+2.02%'),
 ('Apple vs Microsoft operating margin',None,2024,'13.13 percentage points'),
 ('What products did the company launch?', 'NoSuchCompany',2024,'do not contain enough information'),
 ('What were the business risks?', 'Apple',1901,'do not contain enough information'),
]

def main():
 p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8000');args=p.parse_args()
 failures=[]
 for question,company,year,expected in CASES:
  try:
   response=requests.post(args.url+'/api/chat',json={'question':question,'company':company,'year':year},timeout=60)
   data=response.json();passed=response.ok and expected in data.get('answer','')
   detail=data.get('answer') or data.get('message') or 'No answer'
  except requests.RequestException as exc:passed=False;detail=type(exc).__name__
  print(('PASS' if passed else 'FAIL')+' '+question,flush=True)
  if not passed:failures.append((question,expected,detail))
 print(f'{len(CASES)-len(failures)}/{len(CASES)} passed')
 for q,want,actual in failures:print(f'{q}: expected {want!r}; received {actual!r}')
 return bool(failures)

if __name__=='__main__':sys.exit(main())
