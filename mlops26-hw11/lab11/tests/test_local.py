"""Real local HTTP smoke tests; these do not substitute for Kubernetes checks."""
import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from tools.client import attempt
from tools.lab import schedule, summarize


@contextmanager
def server(**env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    log=tempfile.TemporaryFile(mode='w+',encoding='utf-8')
    p=subprocess.Popen([sys.executable,'-m','app.server'],env={**os.environ,'PORT':str(port),'WARMUP_SECONDS':'0.1',**env},stdout=log,stderr=log)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url+'/health',timeout=.2).close(); break
            except Exception:
                if p.poll() is not None:
                    log.seek(0); raise RuntimeError(log.read())
                time.sleep(.05)
        else: raise RuntimeError('Local server did not start')
        yield url
    finally:
        if p.poll() is None: p.terminate()
        p.wait(timeout=10); log.close()


def get(url):
    try:
        with urllib.request.urlopen(url,timeout=5) as r: return r.status,json.load(r)
    except urllib.error.HTTPError as e: return e.code,json.load(e)


def request(url,**body):
    spec=dict(offset=0,request_id='local-check',body=dict(prompt='prefix|input',chunks=3,chunk_delay=.01,**body))
    return attempt(url,spec,time.time(),time.perf_counter())


class LocalTests(unittest.TestCase):
    def test_warmup_and_actual_loaded_hash(self):
        with server(WARMUP_SECONDS='1.2') as url:
            code,data=get(url+'/ready'); self.assertEqual(code,503); self.assertFalse(data['warm'])
            time.sleep(1.4)
            self.assertEqual(get(url+'/ready')[0],200)
            row=request(url)
            self.assertEqual(row['outcome'],'completed')
            self.assertEqual(row['artifact_sha256'],json.loads(Path('artifacts/sha256.json').read_text())['A'])

    def test_broken_probe_is_reproduced(self):
        with server(WARMUP_SECONDS='2',BROKEN_PROBES='true') as url:
            self.assertEqual(get(url+'/ready')[0],200)
            row=request(url)
            self.assertEqual(row['reason'],'unready')

    def test_aborted_response_and_refusal_accounting(self):
        with server() as url:
            time.sleep(.3)
            aborted=request(url,fail_after=1)
            self.assertEqual(aborted['outcome'],'aborted')
            self.assertEqual(aborted['chunks'],1)
            invalid=attempt(url,dict(offset=0,request_id='invalid',body={'prompt':'','chunks':0}),time.time(),time.perf_counter())
            self.assertEqual(invalid['status'],422)
            self.assertEqual(invalid['outcome'],'rejected')

    def test_dialogues_and_version_B(self):
        with server(MODEL_VERSION='B') as url:
            time.sleep(.3)
            for history in [['USER-A'],['USER-B']]:
                row=request(url,history=history)
                self.assertEqual(row['context'],history)
                self.assertEqual(row['version'],'B')

    def test_wrong_artifact_is_never_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'sha256.json').write_text('{"A":"wrong-hash"}')
            Path(tmp,'A.json').write_text('{}')
            with server(ARTIFACT_DIR=tmp) as url:
                time.sleep(.3)
                self.assertEqual(get(url+'/ready')[0],503)

    def test_bounded_queue_rejects_overflow(self):
        with server(MAX_QUEUE='1') as url:
            time.sleep(.3)
            def slow():
                return attempt(url,dict(offset=0,request_id='slow',body=dict(prompt='x',chunks=8,chunk_delay=.2)),time.time(),time.perf_counter())
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                one=pool.submit(slow); time.sleep(.15); two=pool.submit(slow)
                for _ in range(30):
                    if get(url+'/state')[1]['waiting']==1: break
                    time.sleep(.05)
                denied=request(url)
                self.assertEqual(denied['status'],429)
                self.assertEqual(one.result()['outcome'],'completed')
                self.assertEqual(two.result()['outcome'],'completed')

    def test_identical_schedules_and_totals(self):
        left=schedule(120,8,'hot'); right=schedule(120,8,'hot')
        self.assertEqual(left,right)
        self.assertEqual(len(left[0])/sum(map(len,left)),.8)
        summary=summarize([{'outcome':'completed','ttfc':1,'pod':'a'},{'outcome':'not_sent'}])
        self.assertEqual(summary['planned'],2); self.assertEqual(summary['sent'],1)

    def test_forced_exit_interrupts_real_http_stream(self):
        with server() as url:
            time.sleep(.3)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future=pool.submit(attempt,url,dict(offset=0,request_id='crash',body=dict(prompt='long',chunks=40,chunk_delay=.1)),time.time(),time.perf_counter())
                time.sleep(.3)
                with urllib.request.urlopen(urllib.request.Request(url+'/debug/crash',data=b'',method='POST')) as response:
                    self.assertEqual(response.status,200)
                self.assertEqual(future.result(timeout=5)['outcome'],'aborted')


if __name__=='__main__': unittest.main()
