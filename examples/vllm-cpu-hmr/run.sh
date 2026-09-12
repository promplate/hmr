#!/usr/bin/env bash
set -euo pipefail

IMAGE="${VLLM_HMR_IMAGE:-vllm-cpu-hmr-example:local}"
BASE_IMAGE="vllm/vllm-openai-cpu:v0.28.0-x86_64"
PYTH_ON_LINE_SHA="d410f975367e8a29b17183d108ef09a089e42b63"
RESULTS="${VLLM_HMR_RESULTS:-$PWD/vllm-cpu-hmr-results}"
NAME="vllm-cpu-hmr-$(date +%s)"

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  echo "Missing $BASE_IMAGE. Pull it first with: docker pull $BASE_IMAGE" >&2
  exit 1
fi

mkdir -p "$RESULTS"
# The official image runs the probe as root. Keep the bind-mounted receipt
# directory reusable by the invoking user on the next run.
chmod u+rwx,go-rwx "$RESULTS"

docker build --build-arg "PYTH_ON_LINE_SHA=$PYTH_ON_LINE_SHA" --tag "$IMAGE" --file examples/vllm-cpu-hmr/Dockerfile .
IMAGE_ID="$(docker image inspect "$BASE_IMAGE" --format '{{.Id}}')"
IMAGE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')"
PYTH_CORE_SHA="$(docker run --rm --entrypoint python3 "$IMAGE" -c \
  'import hashlib, reactivity.hmr.core as c; print(hashlib.sha256(open(c.__file__, "rb").read()).hexdigest())')"

flock /tmp/hmr-engine-cpu.lock docker run --rm --name "$NAME" --shm-size=4g \
  -v "$RESULTS:/results" \
  "$IMAGE" \
  --source /opt/vllm-release-source \
  --results /results \
  --model "${VLLM_HMR_MODEL:-facebook/opt-125m}" \
  --port 18080 \
  --image "$BASE_IMAGE" \
  --image-id "$IMAGE_ID" \
  --image-digest "$IMAGE_DIGEST" \
  --vllm-version 0.28.0+cpu \
  --installed-source /opt/venv/lib/python3.12/site-packages/vllm \
  --pyth-core-path /opt/venv/lib/python3.12/site-packages/reactivity/hmr/core.py \
  --pyth-core-sha256 "$PYTH_CORE_SHA"
