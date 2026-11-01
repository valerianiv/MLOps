"""Настоящий локальный HTTP-сервер со STUB-моделью."""
import time
import uuid

from integration_tests.harness import Service, WorkspaceCase


class HttpTests(WorkspaceCase):
    def test_http_ready_and_generate_wait_for_warmup(self):
        """Дефект 5: health жив сразу, а обслуживание — только после прогрева."""
        with Service(self.root, warmup=6) as service:
            self.assertEqual(service.get('/health')[0], 200)
            status, _ = service.get('/ready')
            self.assertEqual(status, 503, 'ready успешен до завершения прогрева')
            status, _ = service.post({'prompt': 'вопрос', 'max_new_tokens': 4})
            self.assertEqual(status, 503, 'generate обслужен до завершения прогрева')
            self.assertEqual(service.get('/debug')[1]['calls'], 0, 'backend вызван до готовности')
            service.wait_ready()
            self.assertEqual(service.post({'prompt': 'вопрос', 'max_new_tokens': 4})[0], 200)

    def test_http_bad_requests_never_reach_backend(self):
        """Дефект 7: 4xx до генерации, счётчик backend не меняется; валидный запрос работает."""
        bad = [{'prompt': '', 'max_new_tokens': 4}, {'prompt': '   ', 'max_new_tokens': 4},
               {'max_new_tokens': 4}, {'prompt': 123, 'max_new_tokens': 4},
               {'prompt': 'x', 'max_new_tokens': 0}, {'prompt': 'x', 'max_new_tokens': -1},
               {'prompt': 'x', 'max_new_tokens': 33}, {'prompt': 'x', 'max_new_tokens': 100000},
               {'prompt': 'x', 'max_new_tokens': '4'}, {'prompt': 'x', 'max_new_tokens': True},
               {'prompt': 'x', 'max_new_tokens': 1.5}, {'prompt': 'я' * 5000, 'max_new_tokens': 4}, ['prompt']]
        with Service(self.root) as service:
            service.wait_ready()
            before = service.get('/debug')[1]['calls']
            for payload in bad:
                with self.subTest(payload=str(payload)[:60]):
                    status, _ = service.post(payload)
                    self.assertTrue(400 <= status < 500, f'{status} вместо 4xx')
            self.assertEqual(service.get('/debug')[1]['calls'], before, 'неверный запрос дошёл до backend')
            status, _ = service.post(raw=b'{not json')
            self.assertTrue(400 <= status < 500, f'{status} вместо 4xx на битый JSON')
            status, _ = service.post(raw=b'{"prompt": "' + b'x' * 70000 + b'"}')
            self.assertEqual(status, 413)
            self.assertEqual(service.get('/debug')[1]['calls'], before)
            status, body = service.post({'prompt': 'вопрос', 'max_new_tokens': 32})
            self.assertEqual(status, 200, body)
            self.assertEqual(service.get('/debug')[1]['calls'], before + 1)
            uuid.UUID(body['request_id'])
            self.assertEqual(body['output_tokens'], 1)
            for field in ('model_id', 'runtime_sha256', 'answer', 'generation_seconds', 'queue_seconds', 'total_seconds'):
                self.assertIn(field, body)

    def test_http_serves_loaded_version_until_restart_then_rollback(self):
        """Дефект 6: A → публикация B → всё ещё A → перезапуск → B → откат → перезапуск → A."""
        with Service(self.root) as service:
            service.wait_ready()
            self.assertEqual(service.post({'prompt': 'q'})[1]['model_id'], 'fixture-A')
            done = self.lab('run')
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.accepted_id(), 'fixture-B')
            status, body = service.post({'prompt': 'q'})
            self.assertEqual(status, 200)
            self.assertEqual(body['model_id'], 'fixture-A', 'метаданные не соответствуют модели в памяти')
            self.assertIn('fixture-A', body['answer'])
        with Service(self.root) as service:
            service.wait_ready()
            body = service.post({'prompt': 'q'})[1]
            self.assertEqual((body['model_id'], 'fixture-B' in body['answer']), ('fixture-B', True))
        done = self.lab('rollback', '--to', 'fixture-A')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.accepted_id(), 'fixture-A')
        with Service(self.root) as service:
            service.wait_ready()
            body = service.post({'prompt': 'q'})[1]
            self.assertEqual((body['model_id'], 'fixture-A' in body['answer']), ('fixture-A', True))
        self.assertEqual(len(self.events('fixture-B')), 1)

    def test_http_refuses_artifact_with_wrong_hash(self):
        """Перед загрузкой хеши пересчитываются: подмена файла оставляет сервис неготовым."""
        (self.root / 'model-A.txt').write_text('подменённые веса\n', encoding='utf-8')
        with Service(self.root, warmup=0) as service:
            for _ in range(100):
                status, body = service.get('/ready')
                if status == 200 or body.get('error'):
                    break
                time.sleep(.05)
            self.assertEqual(status, 503, 'сервис готов с подменённым артефактом')
            self.assertIn('Хеш файла', body['error'])
            self.assertEqual(service.post({'prompt': 'q'})[0], 503)
