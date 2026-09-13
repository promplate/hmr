#!/usr/bin/env bash
set -euo pipefail

IMAGE="${VLLM_HMR_IMAGE:-vllm-cpu-hmr-example:local}"
BASE_IMAGE="vllm/vllm-openai-cpu:v0.28.0-x86_64"
PYTH_ON_LINE_SHA="d410f975367e8a29b17183d108ef09a089e42b63"
RESULTS="${VLLM_HMR_RESULTS:-$PWD/vllm-cpu-hmr-results}"
NAME="vllm-cpu-hmr-$(date +%s)-$$"
STAGE="startup"
RUN_EXIT_CODE="null"

# The Dockerfile copies both `packages/vllm-hmr` and this example, so the build context is the
# repository root. Derived from this script's own location rather than the caller's directory:
# invoking it from anywhere else used to fail inside `docker build` with a bare missing-path
# error, after the artifact guard had already claimed the results directory.
EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONTEXT="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"

if ! command -v flock >/dev/null 2>&1; then
  printf '%s\n' 'This runner requires flock (util-linux); install it on the host before running.' >&2
  exit 1
fi
# Held for the whole run so two CPU engines never share the host's cores. Overridable only so
# the runner's own tests can serialise against a private lock instead of blocking on a real run.
exec 9>"${VLLM_HMR_LOCK:-/tmp/hmr-engine-cpu.lock}"
flock 9

mkdir -p "$RESULTS"
# Docker reads a relative -v source as a named volume, so the receipt would land inside that
# volume and the documented output directory would stay empty. Resolved with Bash after mkdir,
# which keeps this working where GNU realpath is absent.
RESULTS="$(cd -- "$RESULTS" && pwd -P)"
for artifact in cpu-smoke-receipt.json cpu-smoke-full.log cpu-source-manifest.json cpu-runner-full.log cpu-container.cid cpu-container-receipt.json; do
  if [[ -e "$RESULTS/$artifact" || -L "$RESULTS/$artifact" ]]; then
    printf 'Refusing to overwrite existing evidence: %s\n' "$RESULTS/$artifact" >&2
    exit 1
  fi
done

cleanup() {
  local status=$?
  # Disarmed first: `docker stop` below can take minutes, and a second signal arriving mid-teardown
  # must not re-enter this function and truncate the receipt it is in the middle of writing.
  trap - EXIT
  trap '' INT TERM
  local cid="" container_created=false container_removed=false cleanup_failed=false remaining=""
  # A signal interrupts `wait` without touching the backgrounded probe, so the `docker run` client
  # would keep running and keep writing to the log this function is about to finish. Killing the
  # client does not stop the container, which is why the cid path below still does the real work.
  if [[ -n "${RUN_PID:-}" ]] && kill -0 "$RUN_PID" 2>/dev/null; then
    kill -TERM "$RUN_PID" 2>/dev/null || true
    wait "$RUN_PID" 2>/dev/null || true
  fi
  if [[ -s "$RESULTS/cpu-container.cid" ]]; then
    cid="$(<"$RESULTS/cpu-container.cid")"
    container_created=true
    # --rm covers the normal path. This is the fallback for an interrupted or disconnected CLI,
    # and it targets this run's own cid: removing by name could hit an unrelated container.
    if remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RESULTS/cpu-runner-full.log")" && [[ -n "$remaining" ]]; then
      docker stop --time 150 "$cid" >>"$RESULTS/cpu-runner-full.log" 2>&1 || true
      docker rm -f "$cid" >>"$RESULTS/cpu-runner-full.log" 2>&1 || true
      if remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RESULTS/cpu-runner-full.log")" && [[ -z "$remaining" ]]; then
        container_removed=true
      else
        cleanup_failed=true
      fi
    else
      container_removed=true
    fi
  fi
  if [[ "$cleanup_failed" == true && "$status" -eq 0 ]]; then
    status=1
  fi
  printf '{"stage":"%s","exit_code":%s,"container_created":%s,"container_id":"%s","run_exit_code":%s,"container_removed":%s,"cleanup_failed":%s}\n' \
    "$STAGE" "$status" "$container_created" "$cid" "$RUN_EXIT_CODE" "$container_removed" "$cleanup_failed" >"$RESULTS/cpu-container-receipt.json"
  exit "$status"
}
# Armed as soon as the guard has claimed the directory, and before anything that can fail leaves
# artifacts in it: every exit from here on writes a receipt naming the stage that failed. The
# previous order installed this only after `docker build`, so a failed build left a full log with
# no receipt beside it — no machine-readable outcome, and the guard blocked the next run.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The official image runs the probe as root. Keep the bind-mounted receipt
# directory reusable by the invoking user on the next run.
STAGE="results_permissions"
chmod u+rwx,go-rwx "$RESULTS"

STAGE="base_image_check"
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  printf 'Missing %s. Pull it first with: docker pull %s\n' "$BASE_IMAGE" "$BASE_IMAGE" >&2
  exit 1
fi

STAGE="build"
# `tee` exits 0 on a failed build, so the build's own status reaches the receipt only through the
# `pipefail` set at the top of this script. Without it a never-produced image would report success.
docker build --build-arg "PYTH_ON_LINE_SHA=$PYTH_ON_LINE_SHA" --tag "$IMAGE" \
  --file "$EXAMPLE_DIR/Dockerfile" "$CONTEXT" 2>&1 | tee "$RESULTS/cpu-runner-full.log"

STAGE="image_metadata"
IMAGE_ID="$(docker image inspect "$BASE_IMAGE" --format '{{.Id}}' 2>>"$RESULTS/cpu-runner-full.log")"
IMAGE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}' 2>>"$RESULTS/cpu-runner-full.log")"
PYTH_CORE_SHA="$(docker run --rm --entrypoint python3 "$IMAGE" -c \
  'import hashlib, reactivity.hmr.core as c; print(hashlib.sha256(open(c.__file__, "rb").read()).hexdigest())' 2>>"$RESULTS/cpu-runner-full.log")"

probe() {
  # `pipefail` again: `tee` must not substitute its own status for the probe's, which is the
  # exit code this whole run is evidence about.
  docker run --rm --name "$NAME" --cidfile "$RESULTS/cpu-container.cid" --shm-size=4g \
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
    --pyth-core-sha256 "$PYTH_CORE_SHA" 2>&1 | tee -a "$RESULTS/cpu-runner-full.log"
}

STAGE="run"
# Backgrounded so `wait` stays interruptible: a SIGTERM addressed to this script alone
# would otherwise be deferred until the probe returns, and a run that lasts minutes would
# produce no receipt. The cleanup trap reaches the container by cid either way.
probe &
RUN_PID=$!
set +e
wait "$RUN_PID"
RUN_EXIT_CODE=$?
set -e
exit "$RUN_EXIT_CODE"
