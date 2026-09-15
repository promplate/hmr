#!/usr/bin/env bash
set -euo pipefail

BASE_IMAGE="xprobe/xinference:latest-cpu"
PYTH_REVISION="d410f975367e8a29b17183d108ef09a089e42b63"
EXPECTED_CORE_SHA256="e89f00a3aaf9ad9451e3fd4e63680d40783a08452f142f494e23f962d0e544eb"
FAMILY="xinference-cpu-hmr"
EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"
MODEL_DIR="${XINFERENCE_HMR_MODEL_DIR:?set XINFERENCE_HMR_MODEL_DIR to a local HF model directory}"
MODE="${XINFERENCE_HMR_MODE:-completion}"
[[ "$MODE" == completion || "$MODE" == chat ]] || { printf 'XINFERENCE_HMR_MODE must be completion or chat\n' >&2; exit 2; }
MODEL_DIR="$(cd -- "$MODEL_DIR" && pwd -P)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${PYTH_REVISION:0:8}-$$-$MODE"
OUT="${XINFERENCE_HMR_RESULTS:-$REPO/.spike-out/parent-verify-runs/$RUN_ID}"
IMAGE="$FAMILY:$RUN_ID"
META_NAME="$FAMILY-meta-$RUN_ID"
SMOKE_NAME="$FAMILY-smoke-$RUN_ID"
RUNNER_LOG="$OUT/runner-full.log"
STAGE="startup"
RUN_EXIT="null"
RUN_PID=""
BASE_IMAGE_ID=""
BASE_IMAGE_DIGEST=""
DERIVED_IMAGE_ID=""

cleanup() {
  local status=$?
  trap - EXIT
  trap '' INT TERM
  mkdir -p "$OUT" 2>/dev/null || true
  if [[ -n "$RUN_PID" ]] && kill -0 "$RUN_PID" 2>/dev/null; then
    kill -TERM "$RUN_PID" 2>/dev/null || true
    wait "$RUN_PID" 2>/dev/null || true
  fi
  local records=() created_total=0 removed_total=0 cleanup_failed=false entry role cidfile name cid remaining created removed failed
  for entry in "meta:$OUT/meta.cid:$META_NAME" "smoke:$OUT/smoke.cid:$SMOKE_NAME"; do
    role="${entry%%:*}"; name="${entry##*:}"; cidfile="${entry#*:}"; cidfile="${cidfile%:*}"
    cid=""; created=false; removed=false; failed=false
    if [[ -s "$cidfile" ]]; then
      cid="$(<"$cidfile")"; created=true; created_total=$((created_total + 1))
      remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RUNNER_LOG" || true)"
      if [[ -n "$remaining" ]]; then
        docker stop --time 60 "$cid" >>"$RUNNER_LOG" 2>&1 || true
        docker rm -f "$cid" >>"$RUNNER_LOG" 2>&1 || true
        remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RUNNER_LOG" || true)"
      fi
      if [[ -z "$remaining" ]]; then removed=true; removed_total=$((removed_total + 1)); else failed=true; cleanup_failed=true; fi
    fi
    records+=("$(printf '{\"role\":\"%s\",\"name\":\"%s\",\"container_created\":%s,\"container_id\":\"%s\",\"container_removed\":%s,\"cleanup_failed\":%s}' "$role" "$name" "$created" "$cid" "$removed" "$failed")")
  done
  local image_untagged=false
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    if docker image rm --no-prune "$IMAGE" >>"$RUNNER_LOG" 2>&1; then image_untagged=true; fi
  fi
  local all_created=false all_removed=false joined
  if [[ "$created_total" -eq 2 ]]; then all_created=true; fi
  if [[ "$created_total" -gt 0 && "$removed_total" -eq "$created_total" ]]; then all_removed=true; fi
  if [[ "$cleanup_failed" == true && "$status" -eq 0 ]]; then status=1; fi
  joined="$(printf '%s,' "${records[@]}")"
  printf '{"run_id":"%s","mode":"%s","stage":"%s","exit_code":%s,"base_image":"%s","base_image_id":"%s","base_image_digest":"%s","derived_image":"%s","derived_image_id":"%s","image_untagged":%s,"host_ports_published":[],"containers":[%s],"container_created":%s,"run_exit":%s,"container_removed":%s,"cleanup_failed":%s}\n' \
    "$RUN_ID" "$MODE" "$STAGE" "$status" "$BASE_IMAGE" "$BASE_IMAGE_ID" "$BASE_IMAGE_DIGEST" "$IMAGE" "$DERIVED_IMAGE_ID" "$image_untagged" "${joined%,}" "$all_created" "$RUN_EXIT" "$all_removed" "$cleanup_failed" >"$OUT/container-receipt.json"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STAGE="output_dir"
if [[ -e "$OUT" || -L "$OUT" ]]; then
  printf 'Refusing to overwrite existing evidence: %s\n' "$OUT" >&2
  exit 1
fi
mkdir -p "$OUT"
chmod 777 "$OUT"
: >"$RUNNER_LOG"

STAGE="residue_guard"
leftovers="$(docker container ls --all --format '{{.ID}} {{.Names}} {{.Status}}' --filter "name=^/?$FAMILY-" 2>>"$RUNNER_LOG" || true)"
if [[ -n "$leftovers" ]]; then
  printf 'Refusing to start: containers from an earlier run remain:\n%s\n' "$leftovers" | tee -a "$RUNNER_LOG" >&2
  exit 1
fi

STAGE="base_image_inspect"
docker image inspect "$BASE_IMAGE" >/dev/null
BASE_IMAGE_ID="$(docker image inspect "$BASE_IMAGE" --format '{{.Id}}')"
BASE_IMAGE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}')"
[[ -n "$BASE_IMAGE_DIGEST" ]] || { printf 'Official base image has no registry digest\n' >&2; exit 1; }

STAGE="build"
docker build --pull=false --build-arg "PYTH_ON_LINE_SHA=$PYTH_REVISION" --tag "$IMAGE" --file "$EXAMPLE_DIR/Dockerfile" "$REPO" 2>&1 | tee -a "$RUNNER_LOG"
DERIVED_IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"

STAGE="metadata"
metadata() {
  docker run --rm --name "$META_NAME" --cidfile "$OUT/meta.cid" --entrypoint python "$IMAGE" -c 'import importlib.metadata as m, subprocess, xinference._version as v; print(m.version("xinference"), v.commit_id, subprocess.check_output(["git", "-C", "/opt/inference", "rev-parse", "HEAD"], text=True).strip(), m.version("hmr"))'
}
read -r XINF_VERSION XINF_COMMIT XINF_GIT_HEAD HMR_CORE_VERSION < <(metadata 2>>"$RUNNER_LOG")
printf 'xinference=%s commit=%s head=%s hmr=%s\n' "$XINF_VERSION" "$XINF_COMMIT" "$XINF_GIT_HEAD" "$HMR_CORE_VERSION" | tee -a "$RUNNER_LOG"

STAGE="smoke"
smoke() {
  flock -w 1800 /tmp/hmr-engine-cpu.lock docker run --rm --name "$SMOKE_NAME" --cidfile "$OUT/smoke.cid" --shm-size=2g \
    -e XINFERENCE_AUTH_ADVANCED=false \
    -e XINFERENCE_ENABLE_VIRTUAL_ENV=0 \
    -e HF_HUB_OFFLINE=1 \
    -e TRANSFORMERS_OFFLINE=1 \
    -v "$OUT:/results" \
    -v "$MODEL_DIR:/models/tiny:ro" \
    --entrypoint python "$IMAGE" /opt/xinference-cpu-hmr/smoke.py \
    --source /opt/inference \
    --results /results \
    --mode "$MODE" \
    --model-path /models/tiny \
    --probe-dir /opt/xinference-cpu-hmr/hmr_xinference_probe \
    --probe-record /results/actor-identity.jsonl \
    --port 9997 \
    --image "$BASE_IMAGE" \
    --image-id "$BASE_IMAGE_ID" \
    --image-digest "$BASE_IMAGE_DIGEST" \
    --xinference-version "$XINF_VERSION" \
    --xinference-commit "$XINF_COMMIT" \
    --xinference-git-head "$XINF_GIT_HEAD" \
    --installed-source /opt/conda/lib/python3.12/site-packages/xinference \
    --hmr-core-version "$HMR_CORE_VERSION" \
    --pyth-revision "$PYTH_REVISION" \
    --expected-core-sha256 "$EXPECTED_CORE_SHA256"
}
smoke 2>&1 | tee "$OUT/smoke-console.log" | tee -a "$RUNNER_LOG" &
RUN_PID=$!
set +e
wait "$RUN_PID"
RUN_EXIT=$?
set -e
RUN_PID=""
exit "$RUN_EXIT"
