"""Compare client p95 to Prometheus for a selected completed run, while TSDB exists.

First port-forward svc/prometheus 19090:9090 in a separate WSL terminal.
"""
import argparse
import json
import time
import urllib.parse
from pathlib import Path
from tools.lab import http_get, save, percentile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('run',help='e.g. results/B/hot-api-1')
    args=parser.parse_args()
    directory=Path(args.run)
    rows=[json.loads(s) for f in directory.glob('client-*.jsonl') for s in f.read_text().splitlines() if s]
    good=[r for r in rows if r['outcome']=='completed']
    if not good: raise SystemExit('No completed responses in this run')
    start=min(r['sent_wall'] for r in rows if 'sent_wall' in r)-1
    end=max(r.get('finished_wall',r.get('sent_wall',start)) for r in rows)+6
    if end>time.time(): time.sleep(end-time.time())
    width=int(end-start)+1
    queries={
        'p95':f'histogram_quantile(0.95,sum by(le)(increase(lab_ttfc_seconds_bucket[{width}s])))',
        'observations':f'sum(increase(lab_ttfc_seconds_count[{width}s]))',
        'buckets':f'sum by(le)(increase(lab_ttfc_seconds_bucket[{width}s]))'}
    result=dict(start=start,end=end,client_completed=len(good),client_p95=percentile([r['ttfc'] for r in good],.95),promql=queries,
        limitation='Same completed-response family and wall interval; increase extrapolates scrape boundaries. Network time and bucket interpolation differ. Inspect counts and terminal logs; not exact equality.')
    for name,q in queries.items():
        result['prometheus_'+name]=http_get('http://127.0.0.1:19090/api/v1/query?'+urllib.parse.urlencode({'query':q,'time':end}))
    save(directory/'reconciliation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
