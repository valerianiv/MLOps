"""Процесс выпуска через subprocess с отдельным рабочим каталогом для каждого теста."""
import json

from integration_tests.harness import FAULT_LEDGER, FAULT_REPLACE, WorkspaceCase, sha256


class ProcessTests(WorkspaceCase):
    def test_cli_recovery_uses_cache_and_input_change_invalidates(self):
        """Дефект 1: сбой → новый процесс берёт кеш; те же путь и новые байты → пересчёт."""
        failed = self.lab('run', '--fail-after-prepare')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('PREPARED', failed.stdout)
        self.assertEqual(self.accepted_id(), 'fixture-A')
        recovered = self.lab('run')
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn('CACHE_HIT', recovered.stdout)
        self.assertNotIn('PREPARED', recovered.stdout)
        (self.root / 'input.txt').write_text('Изменённый пример\n', encoding='utf-8')
        changed = self.lab('run')
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn('PREPARED', changed.stdout, 'изменённый вход взят из старого кеша')
        self.assertNotIn('CACHE_HIT', changed.stdout)

    def test_cli_preparation_settings_invalidate_cache(self):
        """Дефект 1: каждая настройка подготовки участвует в идентичности результата."""
        self.assertIn('PREPARED', self.lab('run', '--fail-after-prepare').stdout)
        config_path = self.root / 'config.json'
        for key, value in [('tokenizer_revision', 'r2'), ('template_sha256', 'f' * 64),
                           ('max_length', 256), ('code_revision', 'prepare-v2')]:
            with self.subTest(key=key):
                config = json.loads(config_path.read_text(encoding='utf-8'))
                config[key] = value
                config_path.write_text(json.dumps(config), encoding='utf-8')
                done = self.lab('run', '--fail-after-prepare')
                self.assertIn('PREPARED', done.stdout, f'{key}: взят старый кеш')

    def test_cli_rejected_reports_keep_pointer_bytes(self):
        """Дефект 2: неполный, отклонённый, чужой отчёт и NaN не меняют принятую версию."""
        report = json.loads((self.root / 'report-B.json').read_text(encoding='utf-8'))
        report['metrics']['accuracy'] = float('nan')
        (self.root / 'report-nan.json').write_text(json.dumps(report), encoding='utf-8')
        report['metrics']['accuracy'] = .8
        report['passed'] = 'false'
        (self.root / 'report-truthy.json').write_text(json.dumps(report), encoding='utf-8')
        before = sha256(self.pointer)
        for name in ['report-incomplete.json', 'report-reject.json', 'report-A.json', 'report-nan.json', 'report-truthy.json']:
            with self.subTest(report=name):
                done = self.lab('run', '--report', name)
                self.assertNotEqual(done.returncode, 0, f'{name}: зелёный выпуск')
                self.assertNotIn('PUBLISHED', done.stdout)
                self.assertEqual(sha256(self.pointer), before, f'{name}: указатель изменён')
        self.assertEqual(self.events('fixture-B'), [])

    def test_cli_disk_error_keeps_pointer_bytes(self):
        """Дефект 3: ошибка замены не портит указатель, не оставляет временных файлов и событий."""
        before = self.pointer.read_bytes()
        done = self.lab('run', fault=FAULT_REPLACE)
        self.assertEqual(self.pointer.read_bytes(), before, 'указатель изменён при ошибке замены')
        self.assertNotEqual(done.returncode, 0, 'ошибка замены не дошла до кода завершения')
        self.assertIn('Инъекция ошибки диска', done.stderr)
        self.assertEqual(list(self.root.glob('*.tmp')), [])
        self.assertEqual(self.events('fixture-B'), [])
        retry = self.lab('run')
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.accepted_id(), 'fixture-B')
        self.assertEqual(len(self.events('fixture-B')), 1)

    def test_cli_tampered_artifact_blocks_release(self):
        """Хеши файлов пересчитываются перед выпуском, отчёт не заменяет проверку весов."""
        before = sha256(self.pointer)
        (self.root / 'model-B.txt').write_text('подменённые веса\n', encoding='utf-8')
        done = self.lab('run')
        self.assertNotEqual(done.returncode, 0, 'выпущен артефакт с неверным хешем')
        self.assertEqual(sha256(self.pointer), before)

    def test_cli_repeat_release_single_event(self):
        """Дефект 4: повторы в других процессах → одно событие выпуска и неизменный указатель."""
        first = self.lab('run')
        self.assertEqual(first.returncode, 0, first.stderr)
        pointer = self.pointer.read_bytes()
        second = self.lab('run')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.events('fixture-B')), 1, 'повтор размножил публикацию')
        self.assertEqual(self.pointer.read_bytes(), pointer)
        (self.root / 'input.txt').write_text('Другой вход той же версии\n', encoding='utf-8')
        self.assertEqual(self.lab('run').returncode, 0)
        self.assertEqual(len(self.events('fixture-B')), 1, 'изменение входа дало второй выпуск')
        attempts = (self.root / 'attempts.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(attempts), 3, 'попытки должны оставаться в отдельном журнале')

    def test_cli_crash_between_pointer_and_journal_recovers_once(self):
        """Дефект 4: сбой между указателем и журналом; повторы дают ровно одно событие."""
        crashed = self.lab('run', fault=FAULT_LEDGER)
        self.assertNotEqual(crashed.returncode, 0)
        self.assertIn('Инъекция сбоя', crashed.stderr)
        self.assertEqual(self.accepted_id(), 'fixture-B')
        self.assertEqual(self.events('fixture-B'), [])
        for _ in range(2):
            retry = self.lab('run')
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(len(self.events('fixture-B')), 1, 'двойное событие после восстановления')
        self.assertEqual(self.accepted_id(), 'fixture-B')
