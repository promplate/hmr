#!/usr/bin/env bash
# A stand-in for the `docker` CLI, so `run.sh`'s failure handling can be exercised for real
# without building an image or starting a container. Installed as `docker` on PATH by the tests.
#
# Container state is a file per container under $MOCK_STATE_DIR, which is also the assertion
# surface: a leaked container is a leftover `container-<cid>.json`.
#
#   MOCK_BASE_IMAGE_MISSING=1  `docker image inspect` fails for the base image
#   MOCK_BUILD_EXIT=N          `docker build` exits N (default 0)
#   MOCK_METADATA_EXIT=N       the post-build `image inspect --format` calls exit N (default 0)
#   MOCK_RUN_EXIT=N            the probe `docker run` exits N (default 0)
#   MOCK_RUN_LEAVE_CONTAINER=1 the probe leaves its container registered, as an interrupted
#                              `--rm` client does, so the cid fallback in cleanup has work to do
#   MOCK_RUN_SLEEP=N           the probe sleeps N seconds before exiting, for signal tests
#   MOCK_RUN_WORKER=1          leave a TERM-resistant worker holding the probe's log pipe open
#   MOCK_RM_FAILS=1            `docker rm` reports success but leaves the container registered
#   MOCK_LS_FAILS=1            `docker container ls` fails, so cleanup cannot confirm removal
set -euo pipefail

BASE_IMAGE="vllm/vllm-openai-cpu:v0.28.0-x86_64"
: "${MOCK_STATE_DIR:?MOCK_STATE_DIR must point at a writable directory}"
mkdir -p "$MOCK_STATE_DIR"
printf '%s\n' "$*" >>"$MOCK_STATE_DIR/calls.log"

subcommand="${1-}"
shift || true

case "$subcommand" in
image)
  [[ "${1-}" == "inspect" ]] || { printf 'mock docker: unhandled `image %s`\n' "${1-}" >&2; exit 2; }
  shift
  image="${1-}"
  shift || true
  format=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --format) format="${2-}"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ "$image" == "$BASE_IMAGE" && "${MOCK_BASE_IMAGE_MISSING-0}" == 1 ]]; then
    printf 'Error response from daemon: No such image: %s\n' "$image" >&2
    exit 1
  fi
  # Only the two metadata reads pass --format, and they are the ones the receipt depends on.
  if [[ -n "$format" && "${MOCK_METADATA_EXIT-0}" != 0 ]]; then
    printf 'mock docker: image inspect --format failed\n' >&2
    exit "${MOCK_METADATA_EXIT}"
  fi
  case "$format" in
    '{{.Id}}') printf 'sha256:%064d\n' 1 ;;
    '{{index .RepoDigests 0}}') printf '%s@sha256:%064d\n' "${image%%:*}" 2 ;;
    '') printf '[{"Id":"sha256:%064d"}]\n' 1 ;;
    *) printf 'mock docker: unhandled format %s\n' "$format" >&2; exit 2 ;;
  esac
  ;;

build)
  printf 'mock docker build %s\n' "$*"
  if [[ "${MOCK_BUILD_EXIT-0}" != 0 ]]; then
    printf 'mock docker build: step failed\n' >&2
    exit "${MOCK_BUILD_EXIT}"
  fi
  printf 'mock docker build: wrote image\n'
  ;;

run)
  cidfile="" name="" entrypoint=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cidfile) cidfile="${2-}"; shift 2 ;;
      --name) name="${2-}"; shift 2 ;;
      --entrypoint) entrypoint="${2-}"; shift 2 ;;
      *) shift ;;
    esac
  done
  # The core-hash read runs `--entrypoint python3` with no cidfile and is not the probe.
  if [[ -n "$entrypoint" ]]; then
    printf '%064d\n' 3
    exit 0
  fi
  [[ -n "$cidfile" ]] || { printf 'mock docker run: probe must pass --cidfile\n' >&2; exit 2; }
  cid="$(printf '%056d%08d' "$$" "$(date +%s)")"
  printf '%s\n' "$cid" >"$cidfile"
  printf '{"id":"%s","name":"%s"}\n' "$cid" "$name" >"$MOCK_STATE_DIR/container-$cid.json"
  printf 'mock docker run: started %s\n' "$name"
  if [[ "${MOCK_RUN_WORKER-0}" == 1 ]]; then
    printf '%s\n' "$$" >"$MOCK_STATE_DIR/probe.pid"
    bash -c 'trap "" TERM; printf "%s\n" "$$" >"$MOCK_STATE_DIR/worker.pid"; exec sleep 30' &
  fi
  [[ "${MOCK_RUN_SLEEP-0}" != 0 ]] && sleep "$MOCK_RUN_SLEEP"
  # `--rm` removes the container on exit on both the success and failure paths; the override
  # models the client losing its connection before that happens.
  [[ "${MOCK_RUN_LEAVE_CONTAINER-0}" == 1 ]] || rm -f "$MOCK_STATE_DIR/container-$cid.json"
  exit "${MOCK_RUN_EXIT-0}"
  ;;

container)
  [[ "${1-}" == "ls" ]] || { printf 'mock docker: unhandled `container %s`\n' "${1-}" >&2; exit 2; }
  shift
  filter=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --filter) filter="${2-}"; shift 2 ;;
      *) shift ;;
    esac
  done
  # `run.sh` must look containers up by id; a name filter would match an unrelated container.
  [[ "$filter" == id=* ]] || { printf 'mock docker: refusing non-id filter %s\n' "$filter" >&2; exit 2; }
  cid="${filter#id=}"
  if [[ "${MOCK_LS_FAILS-0}" == 1 ]]; then
    printf 'mock docker: container ls failed\n' >&2
    exit 9
  fi
  # An unmatched filter is an empty listing, not an error: real `docker container ls` exits 0.
  # `run.sh` distinguishes the two, treating a failed query as "removal unconfirmed".
  if [[ -f "$MOCK_STATE_DIR/container-$cid.json" ]]; then
    printf '%s\n' "$cid"
  fi
  exit 0
  ;;

stop)
  cid=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --time) shift 2 ;;
      -*) shift ;;
      *) cid="$1"; shift ;;
    esac
  done
  [[ -f "$MOCK_STATE_DIR/container-$cid.json" ]] || { printf 'No such container: %s\n' "$cid" >&2; exit 1; }
  printf '%s\n' "$cid"
  ;;

rm)
  cid=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -*) shift ;;
      *) cid="$1"; shift ;;
    esac
  done
  [[ -f "$MOCK_STATE_DIR/container-$cid.json" ]] || { printf 'No such container: %s\n' "$cid" >&2; exit 1; }
  [[ "${MOCK_RM_FAILS-0}" == 1 ]] || rm -f "$MOCK_STATE_DIR/container-$cid.json"
  printf '%s\n' "$cid"
  ;;

*)
  printf 'mock docker: unhandled subcommand %s\n' "$subcommand" >&2
  exit 2
  ;;
esac
