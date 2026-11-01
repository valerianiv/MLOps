"""Снимок базы для Windows из кеша HF, созданного в WSL.

Симлинки WSL не читаются Windows-процессом, поэтому собирается обычный снимок той же
ревизии: жёсткие ссылки на те же blob-файлы (без копии 1.5 ГБ и без правок кеша). Имя
blob — контентный адрес (git SHA-1 или SHA-256), содержимое сверяется с ним.

    python scripts/link_base_snapshot.py --cache D:/hf-cache --hf-home models/hf-home
"""
import argparse
import hashlib
import os
from pathlib import Path

MODEL = 'models--Qwen--Qwen3-0.6B'
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
# Файл ревизии → имя blob; цели симлинков снимка этой ревизии в исходном кеше.
GIT_BLOBS = {'config.json': 'f5c3703b78ae2a478ae15b247e9f855e0ce2107b',
             'generation_config.json': '20a8a9156fc8c3f25295ca067f61fdf120d517c5',
             'merges.txt': '31349551d90c7606f325fe0f11bbb8bd5fa0d7c7',
             'tokenizer_config.json': '417d038a63fa3de29cfde265caedae14d1a58d92',
             'vocab.json': '4783fe10ac3adce15ac8f358ef5462739852c569'}
# Большие файлы: (SHA-256 содержимого = etag HF, имя в общем хранилище hub/blobs).
LFS_BLOBS = {'model.safetensors': ('f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b',
                                   '6f87d3bff66602a9611e541180dec950a6b6a9068ca274ee43ac8f83550f0223'),
             'tokenizer.json': ('aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4',
                                '6aec39639a0a2d1ca966356b8c2b8426a484f80ff80731f44fa8482040713bdf')}


def git_blob_sha1(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cache', type=Path, default=Path('D:/hf-cache'))
    p.add_argument('--hf-home', type=Path, default=Path('models/hf-home'))
    args = p.parse_args()
    target = args.hf_home / 'hub' / MODEL / 'snapshots' / REVISION
    target.mkdir(parents=True, exist_ok=True)
    blobs = args.cache / 'hub' / MODEL / 'blobs'
    shared = args.cache / 'hub' / 'blobs'
    sources = {name: (blobs / blob, blob, git_blob_sha1) for name, blob in GIT_BLOBS.items()}
    sources.update({name: (shared / stored[:2] / stored, etag, file_sha256) for name, (etag, stored) in LFS_BLOBS.items()})
    for name, (source, address, check) in sources.items():
        if check(source) != address:
            raise SystemExit(f'{name}: содержимое blob не совпало с адресом {address}')
        link = target / name
        if not link.exists():
            os.link(source, link)
        print(f'OK {name} {address}')
    print(f'HF_HOME={args.hf_home.resolve()}')


if __name__ == '__main__':
    main()
