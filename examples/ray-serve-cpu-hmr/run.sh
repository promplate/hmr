#!/usr/bin/env bash
# Build the example on the official Ray CPU image and run both stages inside it: the one-item
# preflight first, then the full smoke. Nothing is installed on the host.
#
# Every run gets its own output directory, its own container names and its own cidfiles, and
# writes a machine-readable container receipt naming the stage that ended the run. Residue from
# an earlier run is a hard failure here rather than something this script removes on the way in:
# a stale container or cidfile means an earlier run did not finish its own teardown, and quietly
# deleting it would erase the only evidence of that.
set -euo pipefail

BASE_IMAGE="rayproject/ray:2.58.0-py312-cpu"
PYTH_ON_LINE_SHA="d410f975367e8a29b17183d108ef09a089e42b63"
FAMILY="ray-serve-cpu-hmr"

# Both derived from this script's own location rather than the caller's directory, so it can be
# invoked from anywhere. The Dockerfile `COPY`s nothing, so the example directory is a sufficient
# build context: the repository root would upload the whole checkout, `.venv` included.
EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${PYTH_ON_LINE_SHA:0:8}-$$"
OUT="${HMR_OUT_ROOT:-$REPO/.spike/runs}/$RUN_ID"
RUNNER_LOG="$OUT/runner-full.log"
# A run-unique tag is what the containers are started from, so a pre-existing image can never be
# picked up silently. The second tag is only a build-cache anchor: the build is unconditional and
# the ID it produces is inspected and checked against the base image below.
IMAGE="$FAMILY:$RUN_ID"
CACHE_TAG="$FAMILY:local"
PREFLIGHT_NAME="$FAMILY-preflight-$RUN_ID"
SMOKE_NAME="$FAMILY-smoke-$RUN_ID"

STAGE="startup"
PREFLIGHT_EXIT="null"
SMOKE_EXIT="null"

log() { printf '=== %s\n' "$*" | tee -a "$RUNNER_LOG"; }

cleanup() {
  local status=$?
  # Disarmed first: `docker stop` can take a while, and a second signal must not re-enter this
  # function and truncate the receipt it is in the middle of writing.
  trap - EXIT
  trap '' INT TERM
  mkdir -p "$OUT" 2>/dev/null || true

  # A signal interrupts `wait` without touching the backgrounded client, which would keep writing
  # to the log this function is about to close.
  if [[ -n "${RUN_PID:-}" ]] && kill -0 "$RUN_PID" 2>/dev/null; then
    kill -TERM "$RUN_PID" 2>/dev/null || true
    wait "$RUN_PID" 2>/dev/null || true
  fi

  local created_total=0 removed_total=0 any_failed=false records=() role cidfile name
  for entry in "preflight:$OUT/preflight.cid:$PREFLIGHT_NAME" "smoke:$OUT/smoke.cid:$SMOKE_NAME"; do
    role="${entry%%:*}"
    name="${entry##*:}"
    cidfile="${entry#*:}"
    cidfile="${cidfile%:*}"
    local cid="" created=false removed=false failed=false remaining=""
    if [[ -s "$cidfile" ]]; then
      cid="$(<"$cidfile")"
      created=true
      created_total=$((created_total + 1))
      if remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RUNNER_LOG")" && [[ -n "$remaining" ]]; then
        docker stop --time 60 "$cid" >>"$RUNNER_LOG" 2>&1 || true
        docker rm -f "$cid" >>"$RUNNER_LOG" 2>&1 || true
        if remaining="$(docker container ls --all --quiet --filter "id=$cid" 2>>"$RUNNER_LOG")" && [[ -z "$remaining" ]]; then
          removed=true
        else
          failed=true
        fi
      else
        removed=true
      fi
      if [[ "$removed" == true ]]; then removed_total=$((removed_total + 1)); fi
      if [[ "$failed" == true ]]; then any_failed=true; fi
    fi
    records+=("$(printf '{"role":"%s","name":"%s","container_created":%s,"container_id":"%s","container_removed":%s,"cleanup_failed":%s}' \
      "$role" "$name" "$created" "$cid" "$removed" "$failed")")
  done

  # The run-unique tag is dropped so it cannot be reused; the cache tag keeps the layers.
  local image_untagged=false
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    if docker image rm --no-prune "$IMAGE" >>"$RUNNER_LOG" 2>&1; then image_untagged=true; fi
  fi

  # Written as `if`, not `[[ ... ]] && x=true`: a false test there is a failing AND-list, and
  # under `set -e` that would abort this trap before it writes the receipt.
  local all_created=false all_removed=false
  if [[ "$created_total" -eq 2 ]]; then all_created=true; fi
  if [[ "$created_total" -gt 0 && "$removed_total" -eq "$created_total" ]]; then all_removed=true; fi
  local run_exit="null"
  if [[ "$PREFLIGHT_EXIT" != "null" && "$PREFLIGHT_EXIT" -ne 0 ]]; then
    run_exit="$PREFLIGHT_EXIT"
  elif [[ "$PREFLIGHT_EXIT" != "null" && "$SMOKE_EXIT" != "null" ]]; then
    run_exit="$SMOKE_EXIT"
  fi
  if [[ "$any_failed" == true && "$status" -eq 0 ]]; then status=1; fi

  local joined
  joined="$(printf '%s,' "${records[@]}")"
  printf '{"run_id":"%s","stage":"%s","exit_code":%s,"base_image":"%s","base_image_id":"%s","base_image_digest":"%s","derived_image":"%s","derived_image_id":"%s","image_untagged":%s,"host_ports_published":[],"containers":[%s],"container_created":%s,"run_exit":%s,"container_removed":%s,"cleanup_failed":%s}\n' \
    "$RUN_ID" "$STAGE" "$status" "$BASE_IMAGE" "${BASE_IMAGE_ID:-}" "${BASE_IMAGE_DIGEST:-}" "$IMAGE" "${DERIVED_IMAGE_ID:-}" \
    "$image_untagged" "${joined%,}" "$all_created" "$run_exit" "$all_removed" "$any_failed" \
    >"$OUT/container-receipt.json"
  exit "$status"
}
# Armed before the first side effect of any kind, so every exit from here on leaves a receipt
# naming the stage that ended the run -- including a failure in the residue guard below.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STAGE="output_dir"
if [[ -e "$OUT" ]]; then
  printf 'Run directory already exists, refusing to write into it: %s\n' "$OUT" >&2
  exit 1
fi
mkdir -p "$OUT"
# The container runs as uid 1000 (`ray`) and must stay that way: editing `replica.py` in the
# image's own site-packages is the mechanism under test. So the output directory is opened up
# rather than the container being downgraded to the host uid.
chmod 777 "$OUT"
: >"$RUNNER_LOG"
log "run_id $RUN_ID"
log "out $OUT"

STAGE="residue_guard"
# Fail closed on residue instead of removing it: a leftover container or cidfile is evidence that
# an earlier run did not tear itself down, and `docker rm -f` on the way in would hide that.
# The name filter is a Go regexp, and Docker matches it against the leading-slash form. `\?`
# there is a literal question mark, so the anchored alternative has to be written plainly --
# verified against a container deliberately named into this family.
leftovers="$(docker container ls --all --format '{{.ID}} {{.Names}} {{.Status}}' --filter "name=^/?$FAMILY-" 2>>"$RUNNER_LOG" || true)"
if [[ -n "$leftovers" ]]; then
  printf 'Refusing to start: containers from an earlier run of this example are still present.\n%s\nInspect them, then remove them yourself.\n' "$leftovers" | tee -a "$RUNNER_LOG" >&2
  exit 1
fi
for name in "$PREFLIGHT_NAME" "$SMOKE_NAME"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    printf 'Refusing to start: a container named %s already exists.\n' "$name" | tee -a "$RUNNER_LOG" >&2
    exit 1
  fi
done
for cidfile in "$OUT/preflight.cid" "$OUT/smoke.cid"; do
  if [[ -e "$cidfile" || -L "$cidfile" ]]; then
    printf 'Refusing to start: stale cidfile %s\n' "$cidfile" | tee -a "$RUNNER_LOG" >&2
    exit 1
  fi
done

STAGE="base_image_inspect"
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  printf 'Missing %s. Pull it first with: docker pull %s\n' "$BASE_IMAGE" "$BASE_IMAGE" | tee -a "$RUNNER_LOG" >&2
  exit 1
fi
BASE_IMAGE_ID="$(docker image inspect "$BASE_IMAGE" --format '{{.Id}}' 2>>"$RUNNER_LOG")"
BASE_IMAGE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' 2>>"$RUNNER_LOG")"
if [[ -z "$BASE_IMAGE_DIGEST" ]]; then
  printf 'No RepoDigest on %s: it was not pulled from a registry, so its identity cannot be recorded.\n' "$BASE_IMAGE" | tee -a "$RUNNER_LOG" >&2
  exit 1
fi
# `grep .` drops the trailing empty line Go's `println` leaves behind. Without it the last slot
# of each array is empty, and the prefix comparison below would test the base's empty slot
# against a real derived layer and reject a correctly-built image.
mapfile -t BASE_LAYERS < <(docker image inspect "$BASE_IMAGE" --format '{{range .RootFS.Layers}}{{println .}}{{end}}' 2>>"$RUNNER_LOG" | grep .)
log "base_image $BASE_IMAGE id=$BASE_IMAGE_ID digest=$BASE_IMAGE_DIGEST layers=${#BASE_LAYERS[@]}"

STAGE="build"
# `tee` exits 0 on a failed build, so the build's own status reaches the receipt only through the
# `pipefail` set at the top: without it a never-produced image would report success.
docker build --pull=false --build-arg "PYTH_ON_LINE_SHA=$PYTH_ON_LINE_SHA" \
  --tag "$IMAGE" --tag "$CACHE_TAG" --file "$EXAMPLE_DIR/Dockerfile" "$EXAMPLE_DIR" 2>&1 | tee -a "$RUNNER_LOG"

STAGE="derived_image_lineage"
DERIVED_IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>>"$RUNNER_LOG")"
mapfile -t DERIVED_LAYERS < <(docker image inspect "$IMAGE" --format '{{range .RootFS.Layers}}{{println .}}{{end}}' 2>>"$RUNNER_LOG" | grep .)
log "derived_image $IMAGE id=$DERIVED_IMAGE_ID layers=${#DERIVED_LAYERS[@]}"
# The tag alone proves nothing about what the image was built on. The base's layers must be an
# exact prefix of the derived image's, and the derived image must add at least one: that is what
# rules out a same-named image built from some other base.
if [[ "${#DERIVED_LAYERS[@]}" -le "${#BASE_LAYERS[@]}" ]]; then
  printf 'Derived image has %s layers, base has %s: it is not built on top of %s.\n' \
    "${#DERIVED_LAYERS[@]}" "${#BASE_LAYERS[@]}" "$BASE_IMAGE" | tee -a "$RUNNER_LOG" >&2
  exit 1
fi
for index in "${!BASE_LAYERS[@]}"; do
  if [[ "${DERIVED_LAYERS[index]}" != "${BASE_LAYERS[index]}" ]]; then
    printf 'Layer %s differs: derived %s, base %s. The image was not built from %s.\n' \
      "$index" "${DERIVED_LAYERS[index]}" "${BASE_LAYERS[index]}" "$BASE_IMAGE" | tee -a "$RUNNER_LOG" >&2
    exit 1
  fi
done
log "lineage_ok base layers are an exact prefix of the derived image's"

STAGE="expected_core_sha"
# Computed on the host from the pinned revision's own source tarball, outside every container.
# The smoke compares the bytes it finds installed against this; reading the value out of the
# image instead would only prove the image agrees with itself.
EXPECTED="$(python3 - "$PYTH_ON_LINE_SHA" <<'PY' 2>>"$RUNNER_LOG"
import hashlib, io, re, sys, tarfile, urllib.request

sha = sys.argv[1]
raw = urllib.request.urlopen(f"https://github.com/promplate/pyth-on-line/archive/{sha}.tar.gz", timeout=180).read()
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    member = next(item for item in archive.getmembers() if item.name.endswith("packages/hmr/reactivity/hmr/core.py"))
    extracted = archive.extractfile(member)
    assert extracted is not None
    body = extracted.read()
# `hmr`'s version is `reactivity/hmr/core.py`'s `__version__` (pdm-backend, file source), so the
# pinned revision fixes the version string too and the two can be cross-checked.
version = re.search(rb'^__version__ = "([^"]+)"', body, re.MULTILINE)
assert version is not None, "no __version__ in the pinned core.py"
print(hashlib.sha256(raw).hexdigest())
print(hashlib.sha256(body).hexdigest())
print(version.group(1).decode())
PY
)"
mapfile -t EXPECTED_FIELDS <<<"$EXPECTED"
TARBALL_SHA256="${EXPECTED_FIELDS[0]}"
EXPECTED_CORE_SHA256="${EXPECTED_FIELDS[1]}"
EXPECTED_HMR_VERSION="${EXPECTED_FIELDS[2]}"
log "pinned_source $PYTH_ON_LINE_SHA tarball=$TARBALL_SHA256 core=$EXPECTED_CORE_SHA256 version=$EXPECTED_HMR_VERSION"

STAGE="preflight_one_item"
preflight() {
  docker run --rm --name "$PREFLIGHT_NAME" --cidfile "$OUT/preflight.cid" --shm-size=2g \
    -v "$REPO/.spike/preflight_one_item.py:/preflight_one_item.py:ro" \
    -v "$OUT:/out" \
    -e HMR_PREFLIGHT_JSON=/out/preflight-one-item.json \
    "$IMAGE" python /preflight_one_item.py 2>&1 | tee "$OUT/preflight-one-item.log" | tee -a "$RUNNER_LOG"
}
preflight &
RUN_PID=$!
set +e
wait "$RUN_PID"
PREFLIGHT_EXIT=$?
set -e
RUN_PID=""
if [[ "$PREFLIGHT_EXIT" -ne 0 ]]; then
  printf 'One-item preflight failed (exit %s); not starting the full smoke.\n' "$PREFLIGHT_EXIT" | tee -a "$RUNNER_LOG" >&2
  exit "$PREFLIGHT_EXIT"
fi

STAGE="smoke"
smoke() {
  docker run --rm --name "$SMOKE_NAME" --cidfile "$OUT/smoke.cid" --shm-size=2g \
    -v "$REPO/packages/ray-serve-hmr:/pkg:ro" \
    -v "$EXAMPLE_DIR:/app:ro" \
    -v "$OUT:/out" \
    -v "$HOME/.cache/huggingface:/home/ray/.cache/huggingface" \
    -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" \
    -e HMR_RECEIPT=/out/smoke-receipt.json \
    -e HMR_RUN_ID="$RUN_ID" \
    -e HMR_PYTH_ON_LINE_SHA="$PYTH_ON_LINE_SHA" \
    -e HMR_EXPECTED_CORE_SHA256="$EXPECTED_CORE_SHA256" \
    -e HMR_EXPECTED_HMR_VERSION="$EXPECTED_HMR_VERSION" \
    -e HMR_PINNED_TARBALL_SHA256="$TARBALL_SHA256" \
    -e HMR_BASE_IMAGE="$BASE_IMAGE" \
    -e HMR_BASE_IMAGE_ID="$BASE_IMAGE_ID" \
    -e HMR_BASE_IMAGE_DIGEST="$BASE_IMAGE_DIGEST" \
    -e HMR_DERIVED_IMAGE="$IMAGE" \
    -e HMR_DERIVED_IMAGE_ID="$DERIVED_IMAGE_ID" \
    "$IMAGE" bash -lc '
      set -euo pipefail
      pip install -q --no-deps --no-cache-dir /pkg
      cd /app
      exec python smoke.py
    ' 2>&1 | tee "$OUT/smoke-full.log" | tee -a "$RUNNER_LOG"
}
# Backgrounded so `wait` stays interruptible: a SIGTERM addressed to this script alone would
# otherwise be deferred until the smoke returns, and a run lasting minutes would produce no
# receipt. The cleanup trap reaches the container by cid either way.
smoke &
RUN_PID=$!
set +e
wait "$RUN_PID"
SMOKE_EXIT=$?
set -e
RUN_PID=""
exit "$SMOKE_EXIT"
