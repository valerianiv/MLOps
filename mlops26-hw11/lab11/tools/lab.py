"""Local isolated kind lab. Every Kubernetes action is explicitly contextualized."""
import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTX = 'kind-mlops-lab11'
NS = 'lab11'
K = ['kubectl', '--context', CTX, '-n', NS]
IMAGE = 'lab11-api:1.0.0'
RESULTS = ROOT / 'results'
EVIDENCE = ROOT / 'evidence'
QUERIES = {
    'ttfc_p95': 'histogram_quantile(0.95,sum by(le,pod)(rate(lab_ttfc_seconds_bucket[1m])))',
    'running': 'lab_running', 'waiting': 'lab_waiting',
    'cpu': 'sum by(pod)(rate(container_cpu_usage_seconds_total{namespace="lab11",container="api"}[1m]))',
    'ready': 'kube_deployment_status_replicas_available{namespace="lab11",deployment="api"}',
    'alerts': 'ALERTS',
}


def command(args, check=True, **kwargs):
    p = subprocess.run(args, text=True, encoding='utf-8', capture_output=True, **kwargs)
    if check and p.returncode:
        raise RuntimeError(f'{args}:\n{p.stdout}\n{p.stderr}')
    return p


def k(*args, **kwargs):
    return command(K + list(args), **kwargs).stdout


def obj(*args):
    return json.loads(k(*args, '-o', 'json'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def append(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False) + '\n')


def helm(**values):
    args = ['helm', '--kube-context', CTX, 'upgrade', '--install', 'lab11', 'chart',
            '-n', NS, '--reset-values']
    for key, value in values.items():
        args += ['--set', f'{key}={str(value).lower() if isinstance(value,bool) else value}']
    p = command(args)
    append(EVIDENCE / 'commands.jsonl', dict(wall=time.time(), argv=args, output=p.stdout))


def stable():
    k('rollout', 'status', 'deployment/api', '--timeout=240s')
    deadline=time.time()+120
    while time.time()<deadline:
        pods=api_pods()
        desired=obj('get','deployment','api')['spec']['replicas']
        if len(pods)==desired and all(not p['metadata'].get('deletionTimestamp') and
            any(c.get('type')=='Ready' and c.get('status')=='True' for c in p['status'].get('conditions',[])) for p in pods):
            return
        time.sleep(2)
    raise RuntimeError('Pod set did not settle')


def clients():
    return [f'client-{i}' for i in range(4)]


def remote(code, client='client-0'):
    return k('exec', client, '--', 'python', '-c', code)


def http_get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def probe(url, client='client-0'):
    code = ('import json,urllib.request,urllib.error; '
            f'u={url!r};\ntry:\n r=urllib.request.urlopen(u,timeout=10); '
            'print(json.dumps({"status":r.status,"body":json.load(r)}))\n'
            'except urllib.error.HTTPError as e: '
            'print(json.dumps({"status":e.code,"body":json.load(e)}))')
    return json.loads(remote(code, client))


def api_pods():
    return obj('get', 'pods', '-l', 'app=api')['items']


def snapshot():
    data = {'wall': time.time()}
    for key, resource in [('pods','pods'),('endpoints','endpointslices.discovery.k8s.io'),('hpa','hpa')]:
        try:
            data[key] = obj('get', resource)
        except Exception as exc:
            data[key] = {'error': str(exc)}
    return data


@contextlib.contextmanager
def observe(name):
    directory = RESULTS / name
    if directory.exists():
        raise RuntimeError(f'{directory} exists. Move results to an archive before rerunning this phase.')
    directory.mkdir(parents=True)
    stop = threading.Event()
    pf_log = (directory / 'port-forward.log').open('w')
    pf = subprocess.Popen(K + ['port-forward', 'svc/prometheus', '19090:9090'], stdout=pf_log, stderr=pf_log)
    log_processes={}
    def watch():
        while not stop.is_set():
            snap=snapshot()
            append(directory / 'cluster.jsonl', snap)
            for pod in snap.get('pods',{}).get('items',[]):
                pod_name=pod['metadata']['name']
                if pod['metadata'].get('labels',{}).get('app')!='api' or pod['status'].get('phase')!='Running':
                    continue
                if pod_name not in log_processes or log_processes[pod_name][0].poll() is not None:
                    if pod_name in log_processes:
                        log_processes[pod_name][1].close()
                    handle=(directory/(pod_name+'.log')).open('a',encoding='utf-8')
                    process=subprocess.Popen(K+['logs','-f',pod_name,'--timestamps=true'],stdout=handle,stderr=handle)
                    log_processes[pod_name]=(process,handle)
            record = {'wall': time.time()}
            for key, query in QUERIES.items():
                try:
                    record[key] = http_get('http://127.0.0.1:19090/api/v1/query?' + urllib.parse.urlencode({'query':query}))
                except Exception as exc:
                    record[key] = {'error': str(exc)}
            append(directory / 'prometheus.jsonl', record)
            stop.wait(3)
    thread = threading.Thread(target=watch)
    thread.start()
    try:
        yield directory
    finally:
        stop.set()
        thread.join(timeout=90)
        for process,handle in log_processes.values():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            handle.close()
        (directory / 'events.txt').write_text(k('get','events','--sort-by=.lastTimestamp'), encoding='utf-8')
        pf.terminate()
        pf.wait(timeout=10)
        pf_log.close()


def schedule(seconds=120, rps=8, traffic='uniform', work=1, chunks=8, delay=.03):
    batches = [[] for _ in range(4)]
    for i in range(int(seconds*rps)):
        # Reproducible assignment: 80% from client 0, remainder over other three.
        client = i % 4 if traffic == 'uniform' else (0 if i % 10 < 8 else 1 + (i//10 + i%10)%3)
        batches[client].append(dict(offset=i/rps, request_id=f'req-{i:06d}',
            body=dict(prompt=f'prefix-{i%16}|request-{i}', history=[], chunks=chunks,
                      chunk_delay=delay, work=work), timeout=70))
    return batches


def load(directory, name, batches, service='api', epoch=None, cache_start='not reset; see phase actions'):
    print(f'Load: {name}; service={service}; planned={sum(map(len,batches))}; epoch in ~10s',flush=True)
    target = directory / name
    target.mkdir(parents=True, exist_ok=False)
    save(target / 'schedule.json', batches)
    epoch = epoch or time.time() + 10
    save(target / 'settings.json', dict(epoch=epoch, service=service, keep_alive=False,
        backend_mode='simulated', cache_start=cache_start, attempts_per_request=1))
    def one(i):
        # Every client is a separate Pod/IP. Traffic traverses ClusterIP Service.
        args = K + ['exec','-i',f'client-{i}','--','python','-m','tools.client',
                    '--base',f'http://{service}:8080','--epoch',str(epoch)]
        p = command(args, input=json.dumps(batches[i]), timeout=900)
        (target / f'client-{i}.jsonl').write_text(p.stdout, encoding='utf-8')
        (target / f'client-{i}.stderr').write_text(p.stderr, encoding='utf-8')
        return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for result in pool.map(one, range(4)) for row in result]
    save(target / 'summary.json', summarize(rows))
    return rows


def percentile(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    pos = (len(values)-1)*p
    low = int(pos)
    return values[low] + (values[min(low+1,len(values)-1)]-values[low])*(pos-low)


def summarize(rows):
    from collections import Counter
    good = [r for r in rows if r['outcome']=='completed']
    return dict(planned=len(rows), outcomes=dict(Counter(r['outcome'] for r in rows)),
        completed=len(good), sent=sum(r['outcome']!='not_sent' for r in rows),
        ttfc_p50=percentile([r.get('ttfc') for r in good],.5),
        ttfc_p95=percentile([r.get('ttfc') for r in good],.95),
        error_fraction=1-len(good)/len(rows) if rows else None,
        max_dispatch_lag=max((r.get('dispatch_lag',0) for r in rows),default=0),
        by_pod=dict(Counter(r.get('pod') for r in rows)))


def evidence_check(directory, name, passed, details):
    append(directory / 'checks.jsonl', dict(name=name, passed=bool(passed), details=details, wall=time.time()))
    print(('PASS ' if passed else 'FAIL ') + name, flush=True)


def fresh(**values):
    helm(**values)
    k('rollout', 'restart', 'deployment/api')
    stable()


def setup():
    EVIDENCE.mkdir(exist_ok=True)
    pre = dict(wall=time.time(), disk=shutil.disk_usage(ROOT)._asdict())
    for name, args in [('docker',['docker','info']),('docker_disk',['docker','system','df']),
                       ('kind',['kind','version']),('kubectl',['kubectl','version','--client']),('helm',['helm','version'])]:
        result = command(args, check=(name != 'docker_disk'))
        pre[name] = result.stdout
        if result.returncode:
            pre[name + '_error'] = result.stderr
            pre[name + '_returncode'] = result.returncode
    save(EVIDENCE / 'preflight.json', pre)
    if shutil.disk_usage(ROOT).free < 12 * 1024**3:
        raise RuntimeError('At least 12 GiB free in the working filesystem required; also check Windows/Docker disk.')
    names = command(['kind','get','clusters']).stdout.split()
    if 'mlops-lab11' not in names:
        command(['kind','create','cluster','--name','mlops-lab11','--config','infra/kind.yaml',
                 '--image','kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f','--wait','180s'])
    k('create','namespace',NS,check=False)
    command(['kubectl','--context',CTX,'-n','kube-system','apply','-f','vendor/metrics-server.yaml'])
    patch = {'spec':{'template':{'spec':{'containers':[{'name':'metrics-server', 'args':[
        '--cert-dir=/tmp','--secure-port=10250','--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname',
        '--kubelet-use-node-status-port','--metric-resolution=15s','--kubelet-insecure-tls']}]}}}}
    command(['kubectl','--context',CTX,'-n','kube-system','patch','deployment','metrics-server',
             '--type=strategic','-p',json.dumps(patch)])
    if os.getenv('LAB11_REUSE_IMAGE') == '1':
        cached = command(['docker','exec','mlops-lab11-control-plane','crictl','inspecti',IMAGE])
        save(EVIDENCE / 'reused-image.json', json.loads(cached.stdout))
        print('Using existing application image.', flush=True)
    else:
        command(['docker','build','-t',IMAGE,'.'])
        command(['kind','load','docker-image',IMAGE,'--name','mlops-lab11'])
    helm()
    config = k('create','configmap','grafana-dashboard','--from-file=monitoring/dashboard.json',
               '--dry-run=client','-o','yaml')
    k('apply','-f','-',input=config)
    k('apply','-f','infra/monitoring.yaml')
    for i in range(4):
        pod = {'apiVersion':'v1','kind':'Pod','metadata':{'name':f'client-{i}','labels':{'app':'load-client'}},
            'spec':{'containers':[{'name':'client','image':IMAGE,'imagePullPolicy':'IfNotPresent',
                'command':['python','-c','import time;time.sleep(604800)'],
                'resources':{'requests':{'cpu':'30m','memory':'48Mi'},'limits':{'cpu':'500m','memory':'160Mi'}}}],
                'terminationGracePeriodSeconds':1}}
        k('apply','-f','-',input=json.dumps(pod))
    k('wait','--for=condition=Ready','pod','-l','app=load-client','--timeout=180s')
    for dep in ['api','prometheus','grafana','kube-state']:
        k('rollout','status',f'deployment/{dep}','--timeout=240s')
    command(['kubectl','--context',CTX,'-n','kube-system','rollout','status','deployment/metrics-server','--timeout=180s'])
    save(EVIDENCE / 'versions.json',dict(kubernetes=json.loads(k('version','-o','json')),
         nodes=obj('get','nodes'), images=command(['docker','image','inspect',IMAGE]).stdout,
         clients=obj('get','pods','-l','app=load-client'), values=command(['helm','--kube-context',CTX,'get','values','lab11','-n',NS,'-a']).stdout))
    print('Setup complete. Next: bash scripts/lab.sh check')


def checks():
    with observe('checks') as d:
        fresh(warmup=5)
        old = api_pods()[0]
        original_names={p['metadata']['name'] for p in api_pods()}
        old_name = old['metadata']['name']
        loaded = probe('http://' + old['status']['podIP'] + ':8080/state')['body']
        dialogue=[[dict(offset=0,request_id=f'dialogue-{i}',body=dict(prompt='continue',history=[f'CONTROL-{i}'],chunks=2,chunk_delay=.01,work=1))] for i in range(4)]
        before=load(d,'dialogue-before-replacement',dialogue,old['status']['podIP'])
        # Keep the same explicit history when the old replica disappears.
        k('delete','pod',old_name,'--wait=false')
        deadline = time.time()+90
        observed_cold = False
        while time.time()<deadline:
            for pod in api_pods():
                if pod['metadata']['name'] != old_name and not any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod['status'].get('conditions',[])):
                    ip=pod['status'].get('podIP')
                    if ip:
                        try:
                            cold=probe(f'http://{ip}:8080/ready')
                            observed_cold |= cold['status']==503 and not cold['body']['warm']
                        except Exception:
                            pass
            if observed_cold:
                break
            time.sleep(.5)
        stable()
        replacement=next(p for p in api_pods() if p['metadata']['name'] not in original_names)
        rows=load(d,'dialogue-recovery',dialogue,replacement['status']['podIP'])
        evidence_check(d,'warmup rejects direct readiness',observed_cold,{'old_pod':old_name})
        evidence_check(d,'replacement preserves artifact version', all(r.get('version')=='A' and r.get('artifact_sha256')==loaded['artifact_sha256'] for r in rows),summarize(rows))
        evidence_check(d,'dialogue isolation',all(r.get('context')==[f"CONTROL-{r['request_id'].split('-')[-1]}"] for r in before+rows), rows)
        batches=[[],[],[],[]]
        batches[0]=[dict(offset=0,request_id='injected-abort',body=dict(prompt='abort',chunks=8,fail_after=2)),
                    dict(offset=1,request_id='invalid-body',body=dict(prompt='',chunks=0))]
        bad=load(d,'accounting',batches)
        evidence_check(d,'refusal and incomplete stream accounting',bad[0]['outcome']=='aborted' and any(r.get('status')==422 and r['outcome']=='rejected' for r in bad),bad)


def experiment_a():
    with observe('A') as d:
        # Same incoming schedule for red/green; continuously replace a Pod.
        for label, broken in [('broken',True),('fixed',False)]:
            fresh(brokenProbes=broken,queueTimeout=65)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future=pool.submit(load,d,label,schedule(70,2))
                time.sleep(14)
                k('delete','pod',api_pods()[0]['metadata']['name'],'--wait=false')
                rows=future.result()
            stable()
            evidence_check(d,'warmup-'+label,
                any(r.get('reason')=='unready' for r in rows) if broken else all(r['outcome']=='completed' for r in rows), summarize(rows))
        fresh(queueTimeout=65)
        batches=schedule(100,1)
        batches[0][0]['body'].update(chunks=40,chunk_delay=1)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            f=pool.submit(load,d,'rollout-A-B',batches)
            time.sleep(14)
            helm(modelVersion='B',queueTimeout=65)
            stable()
            rows=f.result()
        evidence_check(d,'graceful rollout has no lost responses',all(r['outcome']=='completed' for r in rows) and any(r.get('version')=='B' for r in rows), summarize(rows))
        helm(modelVersion='B',neverReady=True,queueTimeout=65)
        # Existing B remains serviceable; the newly created B must stay off endpoints.
        time.sleep(28)
        failed=snapshot()
        save(d/'unready-B.json',failed)
        cold_pods=[p['metadata']['name'] for p in failed['pods']['items'] if p['metadata'].get('labels',{}).get('app')=='api'
                   and not any(c.get('type')=='Ready' and c.get('status')=='True' for c in p['status'].get('conditions',[]))]
        active=[e.get('targetRef',{}).get('name') for s in failed['endpoints']['items'] for e in s.get('endpoints',[]) if e.get('conditions',{}).get('ready')]
        evidence_check(d,'never-ready release excluded',bool(cold_pods) and not set(cold_pods)&set(active),{'cold':cold_pods,'ready_endpoints':active})
        # An explicit desired A release is a controlled rollback; do not rely on the last revision being A.
        helm(modelVersion='A',queueTimeout=65)
        stable()
        rows=load(d,'rollback-to-A',schedule(8,2))
        evidence_check(d,'loaded version after rollback', all(r.get('version')=='A' for r in rows) and all(r['outcome']=='completed' for r in rows), rows)
        victim=api_pods()[0]
        # Target known Pod directly to guarantee this stream is interrupted by the kill.
        single=[[dict(offset=0,request_id='forced-kill',body=dict(prompt='long',chunks=40,chunk_delay=1))],[],[],[]]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            f=pool.submit(load,d,'forced-kill',single,victim['status']['podIP'])
            time.sleep(14)
            remote('import urllib.request; '
                   f'print(urllib.request.urlopen(urllib.request.Request("http://{victim["status"]["podIP"]}:8080/debug/crash", data=b"", method="POST")).read().decode())')
            rows=f.result()
        evidence_check(d,'forced kill is counted as aborted',any(r['outcome']=='aborted' for r in rows),rows)
        stable()
        recovered=load(d,'after-kill',schedule(6,1))
        evidence_check(d,'service recovers after process kill',all(r['outcome']=='completed' for r in recovered),summarize(recovered))


def experiment_b(args):
    with observe('B') as d:
        for traffic in ['uniform','hot']:
            batches=schedule(args.seconds,args.rps,traffic)
            save(d/f'input-{traffic}.json',batches)
            for policy in ['api','api-sticky']:
                for repeat in range(1,4):
                    fresh()
                    rows=load(d,f'{traffic}-{policy}-{repeat}',batches,policy,cache_start='empty; complete Pod replacement; backend warm')
                    print(traffic,policy,repeat,json.dumps(summarize(rows)),flush=True)
        save(d/'interpretation-required.json',dict(note='Ordinary Service is the candidate fix. Confirm using all 3 runs: TTFC, errors, queues and distribution. A missing expected skew is also a result; inspect client_ip and connection-close traces.'))


def wait_replicas(number, timeout):
    deadline=time.time()+timeout
    while time.time()<deadline:
        dep=obj('get','deployment','api')
        if dep['spec']['replicas']==number and dep['status'].get('readyReplicas',0)==number and dep['status'].get('replicas',0)==number:
            return dict(wall=time.time(),deployment=dep,hpa=obj('get','hpa','api'))
        time.sleep(3)
    return {'error':f'No stable {number} replicas within {timeout}s','wall':time.time()}


def experiment_c(args):
    with observe('C') as d:
        fresh(**{'hpa.enabled':True})
        save(d/'resource-metrics.json', json.loads(k('get','--raw','/apis/metrics.k8s.io/v1beta1/pods')))
        before=time.time()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            f=pool.submit(load,d,'scale-out',schedule(180,args.hpa_rps,'uniform',work=8,chunks=8,delay=.02))
            up=wait_replicas(3,200)
            rows=f.result()
        save(d/'scale-up.json',up)
        evidence_check(d,'HPA reaches 3 ready replicas','error' not in up,up)
        low_start=time.time()
        down=wait_replicas(2,360)
        save(d/'scale-down.json',dict(load_start=before,load_end=low_start,observed_wait=time.time()-low_start,result=down))
        evidence_check(d,'HPA returns to 2 ready replicas','error' not in down,down)
        load(d,'sticky-hot-after-scale',schedule(120,args.rps,'hot',work=4),'api-sticky')
        # Fixed two replicas for repeatable alert stimulus; very small waiting room.
        fresh(maxQueue=1)
        load(d,'alert-stimulus',schedule(140,args.hpa_rps,'uniform',work=8,chunks=20,delay=.12))
        load(d,'alert-recovery',schedule(100,1,'uniform',work=1,chunks=1,delay=0))
        # Observer keeps raw pending/firing and disappearance. Verify actual transitions.
        time.sleep(70)
        records=[json.loads(line) for line in (d/'prometheus.jsonl').read_text().splitlines()]
        states={}
        for rec in records:
            for item in rec.get('alerts',{}).get('data',{}).get('result',[]):
                metric=item['metric']
                states.setdefault(metric.get('alertname'),set()).add(metric.get('alertstate'))
        for name in ['LabHighTTFC','LabRefusals']:
            final=records[-1].get('alerts',{}).get('data',{}).get('result',[])
            recovered=not any(x['metric'].get('alertname')==name for x in final)
            evidence_check(d,name+' pending/firing/recovered',{'pending','firing'}<=states.get(name,set()) and recovered,dict(states=sorted(states.get(name,set())),recovered=recovered))


def inventory():
    out=dict(wall=time.time(),disk=shutil.disk_usage(ROOT)._asdict())
    for name,args in [('docker',['docker','system','df','-v']),('pods',K+['get','pods','-o','wide']),('usage',K+['top','pods']),('images',['docker','image','ls','--digests'])]:
        p=command(args,check=False)
        out[name]=p.stdout+p.stderr
    save(EVIDENCE/'final-inventory.json',out)
    (EVIDENCE/'helm-manifest.yaml').write_text(command(['helm','--kube-context',CTX,'get','manifest','lab11','-n',NS]).stdout,encoding='utf-8')
    command(['kind','export','logs',str(EVIDENCE/'kind-logs'),'--name','mlops-lab11'])


def main():
    os.chdir(ROOT)
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['setup','check','a','b','c','all','inventory','report','cleanup'])
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--rps',type=float,default=6)
    parser.add_argument('--hpa-rps',type=float,default=24)
    args=parser.parse_args()
    if args.seconds<120:
        parser.error('Comparisons require at least 120 measurement seconds.')
    if args.action=='setup': setup()
    elif args.action=='check': checks()
    elif args.action=='a': experiment_a()
    elif args.action=='b': experiment_b(args)
    elif args.action=='c': experiment_c(args)
    elif args.action=='all':
        checks(); experiment_a(); experiment_b(args); experiment_c(args); inventory()
        command([sys.executable,'-m','tools.report'])
    elif args.action=='inventory': inventory()
    elif args.action=='report':
        p=command([sys.executable,'-m','tools.report']); print(p.stdout)
    elif args.action=='cleanup':
        command(['kind','delete','cluster','--name','mlops-lab11'])
        print('Only mlops-lab11 cluster deleted. Results and Docker image are kept.')


if __name__=='__main__':
    main()
