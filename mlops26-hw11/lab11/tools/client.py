"""Open-loop client running INSIDE a client Pod. stdout is JSONL only."""
import argparse
import concurrent.futures
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request


def attempt(base, spec, origin_wall, origin_mono):
    scheduled = origin_mono + spec['offset']
    started = time.perf_counter()
    row = dict(spec, scheduled_wall=origin_wall + spec['offset'],
               sent_wall=time.time(), sent_mono=started, planned_mono=scheduled,
               dispatch_lag=max(0, started - scheduled), client=socket.gethostname(),
               status=None, outcome='transport_error', chunks=0, ttfc=None,
               duration=None, pod=None, version=None, artifact_sha256=None,
               backend_mode='simulated', attempts=1, keep_alive=False)
    payload = spec['body']
    req = urllib.request.Request(base.rstrip('/') + '/stream',
        data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'X-Request-ID': spec['request_id'],
                 'Connection': 'close'})
    first = None
    done = False
    previous = None
    intervals = []
    try:
        with urllib.request.urlopen(req, timeout=spec.get('timeout', 70)) as response:
            row['status'] = response.status
            for line in response:
                item = json.loads(line)
                for key in ('pod', 'version', 'artifact_sha256', 'backend_mode', 'client_ip'):
                    row[key] = item.get(key)
                if item['type'] == 'chunk':
                    now = time.perf_counter()
                    first = now if first is None else first
                    if previous is not None:
                        intervals.append(now - previous)
                    previous = now
                    row['chunks'] += 1
                    row['queue_seconds'] = item['queue_seconds']
                    row['cache_hit'] = item['cache_hit']
                    row['context'] = item['context']
                elif item['type'] == 'done':
                    done = True
        row['outcome'] = 'completed' if done and row['chunks'] == payload['chunks'] else 'aborted'
    except urllib.error.HTTPError as exc:
        row['status'] = exc.code
        row['outcome'] = 'rejected'
        try:
            data = json.loads(exc.read())
            row.update({k: data[k] for k in ('pod','version','artifact_sha256','client_ip') if k in data})
            row['reason'] = data.get('error')
        except Exception:
            pass
    except (TimeoutError, socket.timeout) as exc:
        row.update(outcome='timeout', error=str(exc))
    except Exception as exc:
        row.update(outcome='aborted' if row['status'] == 200 else 'transport_error', error=str(exc))
    row['duration'] = time.perf_counter() - started
    row['finished_wall'] = time.time()
    row['ttfc'] = first - started if first is not None else None
    row['inter_chunk_mean'] = sum(intervals) / len(intervals) if intervals else None
    return row


def run(base, schedule, epoch):
    # Monotonic clock for durations; wall clock only aligns clients and plots.
    mono = time.perf_counter() + epoch - time.time()
    lock = threading.Lock()
    pending = threading.BoundedSemaphore(128)
    def send(spec):
        try:
            result = attempt(base, spec, epoch, mono)
            with lock:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            pending.release()
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        for spec in schedule:
            time.sleep(max(0, mono + spec['offset'] - time.perf_counter()))
            if not pending.acquire(blocking=False):
                print(json.dumps(dict(spec, outcome='not_sent', client=socket.gethostname(),
                                      scheduled_wall=epoch+spec['offset'], reason='generator_capacity')), flush=True)
            else:
                pool.submit(send, spec)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--epoch', type=float, required=True)
    args = parser.parse_args()
    run(args.base, json.load(sys.stdin), args.epoch)
