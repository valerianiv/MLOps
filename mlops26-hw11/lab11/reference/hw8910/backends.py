"""Backend сервиса. Stub и Transformers реализуют один интерфейс:

loaded_id, runtime_sha256, calls, warmup(), count_input_tokens(prompt), generate(prompt, limit).
"""
import time

FIXTURE_REVISION = 'fixture-only'


class StubBackend:
    name = 'stub'

    def __init__(self, manifest, root):
        if manifest['runtime'].get('base_revision') != FIXTURE_REVISION:
            raise ValueError('StubBackend обслуживает только фикстуры; реальный выпуск: --backend transformers')
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.calls = 0

    def warmup(self):
        pass

    def count_input_tokens(self, prompt):
        return len(prompt.split())

    def generate(self, prompt, limit):
        self.calls += 1
        time.sleep(.01)
        return {'answer': f'STUB ONLY: {self.loaded_id}', 'output_tokens': 1}


class TransformersBackend:
    """Те же условия, что в evaluation/cli.py ДЗ 6+7: шаблон, system, greedy, dtype."""
    name = 'transformers'

    def __init__(self, manifest, root):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        import release
        runtime = manifest['runtime']
        if runtime.get('base_revision') == FIXTURE_REVISION:
            raise ValueError('Фикстура STUB не является моделью')
        self.torch = torch
        self.generation = runtime['generation']
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.calls = 0
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        artifact = release.artifact_root(manifest, root)
        base_uri = {'artifact_uri': f'hf://{runtime["base_name"]}@{runtime["base_revision"]}'}
        snapshot = release.artifact_root(base_uri, root)
        adapter = manifest.get('kind') == 'adapter'
        self.tokenizer = AutoTokenizer.from_pretrained(artifact if adapter else snapshot, local_files_only=True)
        self._check_template(runtime, release)
        torch.manual_seed(self.generation['seed'])
        model = AutoModelForCausalLM.from_pretrained(snapshot, local_files_only=True, dtype=getattr(torch, runtime['dtype']))
        if adapter:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, artifact)
        self.model = model.to(self.device).eval()

    def _check_template(self, runtime, release):
        template = self.tokenizer.chat_template
        if release.digest(template.encode('utf-8')) != runtime['template_sha256']:
            raise ValueError('Шаблон токенизатора не совпадает с условиями оценки')

    def _prompt_text(self, prompt):
        return self.tokenizer.apply_chat_template(
            [{'role': 'system', 'content': self.generation['system']}, {'role': 'user', 'content': prompt}],
            tokenize=False, add_generation_prompt=True, enable_thinking=self.generation['enable_thinking'])

    def count_input_tokens(self, prompt):
        return len(self.tokenizer(self._prompt_text(prompt))['input_ids'])

    def warmup(self):
        self._run('Warmup.', 1)

    def _run(self, prompt, limit):
        batch = self.tokenizer(self._prompt_text(prompt), return_tensors='pt').to(self.device)
        with self.torch.inference_mode():
            out = self.model.generate(**batch, max_new_tokens=limit, do_sample=False,
                                      pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        tail = out[0, batch['input_ids'].shape[1]:]
        return {'answer': self.tokenizer.decode(tail, skip_special_tokens=True), 'output_tokens': int(tail.shape[0])}

    def generate(self, prompt, limit):
        self.calls += 1
        return self._run(prompt, limit)


FACTORIES = {'stub': StubBackend, 'transformers': TransformersBackend}
