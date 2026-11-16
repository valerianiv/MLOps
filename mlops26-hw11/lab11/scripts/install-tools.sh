#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -m)" != x86_64 ]; then echo 'This installer targets x86_64 Ubuntu/WSL.'; exit 1; fi
for command in curl tar sha256sum python3 docker; do
  command -v "$command" >/dev/null || { echo "Missing: $command (see README)"; exit 1; }
done
mkdir -p .tools vendor evidence
curl -fL --retry 3 https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64 -o .tools/kind
curl -fL --retry 3 https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64.sha256sum -o .tools/kind.sha256
echo "$(cut -d ' ' -f1 .tools/kind.sha256)  .tools/kind" | sha256sum -c -
curl -fL --retry 3 https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl -o .tools/kubectl
curl -fL --retry 3 https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl.sha256 -o .tools/kubectl.sha256
echo "$(cat .tools/kubectl.sha256)  .tools/kubectl" | sha256sum -c -
curl -fL --retry 3 https://get.helm.sh/helm-v3.17.2-linux-amd64.tar.gz -o .tools/helm.tar.gz
curl -fL --retry 3 https://get.helm.sh/helm-v3.17.2-linux-amd64.tar.gz.sha256sum -o .tools/helm.sha256
echo "$(cut -d ' ' -f1 .tools/helm.sha256)  .tools/helm.tar.gz" | sha256sum -c -
tar -xzf .tools/helm.tar.gz -C .tools linux-amd64/helm
cp .tools/linux-amd64/helm .tools/helm
chmod +x .tools/kind .tools/kubectl .tools/helm
curl -fL --retry 3 https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml -o vendor/metrics-server.yaml
sha256sum .tools/kind .tools/kubectl .tools/helm vendor/metrics-server.yaml > evidence/downloads.sha256
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
echo 'Tools ready. Next: bash scripts/lab.sh setup'
