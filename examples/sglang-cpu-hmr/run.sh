#!/usr/bin/env bash
set -euo pipefail

IMAGE="${SGLANG_HMR_IMAGE:-sglang-cpu-hmr-example:local}"
BASE_IMAGE="lmsysorg/sglang:v0.5.16-xeon"
PYTH_ON_LINE_SHA="d410f975367e8a29b17183d108ef09a089e42b63"
RESULTS="${SGLANG_HMR_RESULTS:-$PWD/sglang-cpu-hmr-results}"
NAME="sglang-cpu-hmr-$(date +%s)"

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  echo "Missing $BASE_IMAGE. Pull it first with: docker pull $BASE_IMAGE" >&2
  exit 1
fi

mkdir -p "$RESULTS"
chmod u+rwx,go-rwx "$RESULTS"

docker build --build-arg "PYTH_ON_LINE_SHA=$PYTH_ON_LINE_SHA" --tag "$IMAGE" --file examples/sglang-cpu-hmr/Dockerfile .
IMAGE_ID="$(docker image inspect "$BASE_IMAGE" --format '{{.Id}}')"
IMAGE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')"
PYTH_CORE_SHA="$(docker run --rm --entrypoint python3 "$IMAGE" -c \
  'import hashlib, reactivity.hmr.core as c; print(hashlib.sha256(open(c.__file__, "rb").read()).hexdigest())')"

flock /tmp/hmr-engine-cpu.lock docker run --rm --name "$NAME" --shm-size=4g \
  -v "$RESULTS:/results" \
  "$IMAGE" \
  --source /opt/sglang-release-source \
  --results /results \
  --model "${SGLANG_HMR_MODEL:-facebook/opt-125m}" \
  --port 31000 \
  --image "$BASE_IMAGE" \
  --image-id "$IMAGE_ID" \
  --image-digest "$IMAGE_DIGEST" \
  --sglang-version 0.5.16 \
  --installed-source /opt/.venv/lib/python3.12/site-packages/sglang \
  --pyth-core-path /opt/.venv/lib/python3.12/site-packages/reactivity/hmr/core.py \
  --pyth-core-sha256 "$PYTH_CORE_SHA"
