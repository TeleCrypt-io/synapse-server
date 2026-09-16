#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GH_API_VERSION:?GH_API_VERSION is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${RELEASE_RECORD:?RELEASE_RECORD is required}"
: "${RELEASE_ASSET_NAME:?RELEASE_ASSET_NAME is required}"
: "${EXPECTED_TAG:?EXPECTED_TAG is required}"
: "${EXPECTED_SHA:?EXPECTED_SHA is required}"
: "${EXPECTED_ANNOTATED_TAG_SHA:?EXPECTED_ANNOTATED_TAG_SHA is required}"
: "${EXPECTED_DIGEST:?EXPECTED_DIGEST is required}"

readonly REPOSITORY='TeleCrypt-io/synapse-server-container'
readonly IMAGE='ghcr.io/telecrypt-io/telecrypt-synapse'
readonly RELEASE_BODY="Exact Synapse release for source commit $EXPECTED_SHA."
readonly MAX_RECORD_BYTES=$((64 * 1024))
readonly MAX_ASSET_BYTES=$((64 * 1024))
readonly REQUIRED_API_VERSION='2026-03-10'

test "$GH_API_VERSION" = "$REQUIRED_API_VERSION"
test "$GITHUB_REPOSITORY" = "$REPOSITORY"
[[ "$EXPECTED_TAG" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-tc[1-9][0-9]*$ ]]
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$EXPECTED_ANNOTATED_TAG_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
test "$RELEASE_ASSET_NAME" = "telecrypt-synapse-$EXPECTED_TAG.digest.json"

record_size="$(wc -c <"$RELEASE_RECORD")"
test "$record_size" -gt 0 -a "$record_size" -le "$MAX_RECORD_BYTES"
record_digest="sha256:$(sha256sum "$RELEASE_RECORD" | awk '{print $1}')"
jq -e -s 'length == 1 and (.[0] | type == "object" and
  (keys == ["annotated_tag_sha", "digest", "image", "schema_version", "source_commit", "tag"]))' \
  "$RELEASE_RECORD" >/dev/null
jq -e --arg tag "$EXPECTED_TAG" --arg digest "$EXPECTED_DIGEST" --arg source "$EXPECTED_SHA" \
  --arg tag_object "$EXPECTED_ANNOTATED_TAG_SHA" --arg image "$IMAGE" \
  'type == "object" and .image == $image and .tag == $tag and .digest == $digest and
   .source_commit == $source and .annotated_tag_sha == $tag_object and .schema_version == 1' \
  "$RELEASE_RECORD" >/dev/null
canonical_record="$(mktemp)"
release_json="$(mktemp)"
release_error="$(mktemp)"
downloaded_asset="$(mktemp)"
release_headers="$(mktemp)"
release_page_headers="$(mktemp)"
release_page_json="$(mktemp)"
release_matches="$(mktemp)"
release_id=''
cleanup() {
  local failed=0 path
  for path in "$canonical_record" "$release_json" "$release_error" "$downloaded_asset" \
    "$release_headers" "$release_page_headers" "$release_page_json" "$release_matches" \
    "$release_json.create.log" "$release_json.create.error"; do
    if ! rm -f -- "$path"; then
      echo "ERROR: could not remove release publication temporary file: $path" >&2
      failed=1
    fi
  done
  return "$failed"
}
on_exit() {
  local status=$? cleanup_status
  set +e
  cleanup
  cleanup_status=$?
  if [[ "$status" -eq 0 && "$cleanup_status" -ne 0 ]]; then
    return "$cleanup_status"
  fi
  return "$status"
}
trap on_exit EXIT
active_command_pid=''
replay_diagnostics() {
  local path status=0
  for path in "$release_json" "$release_error" "$release_headers" "$release_page_headers" \
    "$release_page_json" "$release_matches" "$release_json.create.log" "$release_json.create.error"; do
    if [[ -f "$path" ]] && ! cat -- "$path" >&2; then
      echo "ERROR: could not replay release publication diagnostics: $path" >&2
      status=1
    fi
  done
  return "$status"
}
on_signal() {
  local signal_name="$1" exit_status=143 probe_status=0 kill_status=0 wait_status=0 replay_status=0 cleanup_status=0
  case "$signal_name" in
    HUP) exit_status=129 ;;
    INT) exit_status=130 ;;
  esac
  set +e
  trap - EXIT HUP INT TERM
  if [[ -n "$active_command_pid" ]]; then
    kill -0 "$active_command_pid" 2>/dev/null
    probe_status=$?
    if [[ "$probe_status" -eq 0 ]]; then
      kill -"$signal_name" "$active_command_pid"
      kill_status=$?
      if [[ "$kill_status" -ne 0 ]]; then
        printf 'ERROR: release publication child termination failed during %s (status %s)\n' "$signal_name" "$kill_status" >&2
      fi
    elif [[ "$probe_status" -ne 1 ]]; then
      printf 'ERROR: release publication child liveness check failed during %s (status %s)\n' "$signal_name" "$probe_status" >&2
    fi
    wait "$active_command_pid"
    wait_status=$?
    if [[ "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" ]]; then
      printf 'ERROR: release publication child wait failed during %s (status %s)\n' "$signal_name" "$wait_status" >&2
    fi
    active_command_pid=''
  fi
  replay_diagnostics || replay_status=$?
  cleanup || cleanup_status=$?
  if [[ "$probe_status" -gt 1 || "$kill_status" -ne 0 || "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" || "$replay_status" -ne 0 || "$cleanup_status" -ne 0 ]]; then
    exit_status=1
  fi
  exit "$exit_status"
}
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
jq -cS . "$RELEASE_RECORD" >"$canonical_record"
cmp "$RELEASE_RECORD" "$canonical_record"

capture_command() {
  local output="$1" stderr="$2" timeout_seconds="$3" status
  shift 3
  rm -f -- "$output" "$stderr"
  set +e
  timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" "$@" >"$output" 2>"$stderr" &
  active_command_pid=$!
  wait "$active_command_pid"
  status=$?
  active_command_pid=''
  set -e
  if [[ "$status" -eq 0 && -s "$stderr" ]]; then
    cat -- "$stderr" >&2
  fi
  return "$status"
}

http_status() {
  local count
  count="$(grep -Ec '^HTTP/[0-9.]+[[:space:]][0-9]{3}([[:space:]]|$)' "$1")"
  test "$count" -eq 1
  sed -n '1s/^HTTP\/[0-9.]*[[:space:]]\([0-9][0-9][0-9]\).*$/\1/p' "$1"
}

http_body() {
  awk 'BEGIN { body = 0 } { line = $0; sub(/\r$/, "", line); if (!body) { if (line == "") body = 1; next } print }' "$1"
}

discover_release_id() {
  local page page_size complete=0 command_status code match_count
  : >"$release_matches"
  for (( page=1; ; page++ )); do
    if capture_command "$release_page_headers" "$release_error" 30 \
      gh api --include --hostname github.com --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      "repos/$REPOSITORY/releases?per_page=100&page=$page"; then
      command_status=0
    else
      command_status=$?
    fi
    if [[ "$command_status" -ne 0 ]]; then
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    fi
    if ! code="$(http_status "$release_page_headers")"; then
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    fi
    if [[ "$code" != 200 ]]; then
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    fi
    if ! http_body "$release_page_headers" >"$release_page_json"; then
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    fi
    jq -e 'type == "array" and length <= 100 and all(.[]; type == "object" and (.tag_name | type == "string"))' \
      "$release_page_json" >/dev/null || {
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    }
    jq -c --arg tag "$EXPECTED_TAG" '.[] | select(.tag_name == $tag)' \
      "$release_page_json" >>"$release_matches" || {
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    }
    if ! page_size="$(jq -er 'length' "$release_page_json")"; then
      cat -- "$release_page_headers" "$release_error" >&2
      return 1
    fi
    if (( page_size < 100 )); then
      complete=1
      break
    fi
  done
  if [[ "$complete" -ne 1 ]]; then
    cat -- "$release_page_headers" "$release_error" >&2
    return 1
  fi
  if ! match_count="$(wc -l <"$release_matches")"; then
    cat -- "$release_page_headers" "$release_error" >&2
    return 1
  fi
  case "$match_count" in
    0)
      return 4
      ;;
    1)
      release_id="$(jq -s -er '.[0].id | select(type == "number" and . > 0 and . == floor)' "$release_matches")"
      [[ "$release_id" =~ ^[1-9][0-9]*$ ]]
      ;;
    *)
      echo 'release list contains multiple exact tag matches' >&2
      return 1
      ;;
  esac
}

get_release_by_id() {
  local command_status code
  if capture_command "$release_headers" "$release_error" 30 \
    gh api --include --hostname github.com --header 'Accept: application/vnd.github+json' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/$release_id"; then
    command_status=0
  else
    command_status=$?
  fi
  if [[ "$command_status" -ne 0 ]]; then
    cat -- "$release_headers" "$release_error" >&2
    return 1
  fi
  if ! code="$(http_status "$release_headers")"; then
    cat -- "$release_headers" "$release_error" >&2
    return 1
  fi
  case "$code" in
    200)
      if ! http_body "$release_headers" >"$release_json"; then
        cat -- "$release_headers" "$release_error" >&2
        return 1
      fi
      jq -e --argjson release_id "$release_id" \
        'type == "object" and .id == $release_id' "$release_json" >/dev/null || {
        cat -- "$release_headers" "$release_error" >&2
        return 1
      }
      return 0
      ;;
    *)
      cat -- "$release_headers" "$release_error" >&2
      return 1
      ;;
  esac
}

get_release() {
  discover_release_id || return $?
  get_release_by_id
}

check_draft() {
  jq -e --argjson release_id "$release_id" --argjson max_asset_bytes "$MAX_ASSET_BYTES" \
    --arg tag "$EXPECTED_TAG" --arg body "$RELEASE_BODY" --arg asset "$RELEASE_ASSET_NAME" '
    type == "object" and .id == $release_id and .tag_name == $tag and
    .name == $tag and .body == $body and .draft == true and .prerelease == false and
    (.assets|type == "array" and length <= 1) and
    ((.assets|length) == 0 or
      ((.assets|length) == 1 and .assets[0].name == $asset and
       .assets[0].label == "" and
       .assets[0].state == "uploaded" and
       (.assets[0].id | type == "number" and . > 0 and . == floor) and
       (.assets[0].size | type == "number" and . > 0 and . == floor and . <= $max_asset_bytes)))
  ' "$release_json" >/dev/null
}

if get_release; then
  if ! check_draft; then
    replay_diagnostics
    echo 'pre-existing release does not match the exact recoverable draft contract' >&2
    exit 1
  fi
else
  status=$?
  if [[ "$status" -ne 4 ]]; then
    cat -- "$release_page_headers" "$release_error" >&2
    printf 'release discovery failed (status %s)\n' "$status" >&2
    exit 1
  fi
  if capture_command "$release_json.create.log" "$release_json.create.error" 60 \
    gh api --include --hostname github.com --method POST \
      --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      --field "tag_name=$EXPECTED_TAG" --field "target_commitish=$EXPECTED_SHA" \
      --field "name=$EXPECTED_TAG" --field "body=$RELEASE_BODY" \
      --field draft=true --field prerelease=false \
      "repos/$REPOSITORY/releases"; then
    create_status=0
  else
    create_status=$?
  fi
  if [[ "$create_status" -ne 0 ]]; then
    cat -- "$release_json.create.log" "$release_json.create.error" >&2
    printf 'release draft creation failed (status %s)\n' "$create_status" >&2
    exit 1
  fi
  if ! create_code="$(http_status "$release_json.create.log")" || [[ "$create_code" != 201 ]]; then
    cat -- "$release_json.create.log" "$release_json.create.error" >&2
    echo 'release draft creation did not return one HTTP 201 response' >&2
    exit 1
  fi
  if ! http_body "$release_json.create.log" >"$release_json" \
    || ! release_id="$(jq -er '.id | select(type == "number" and . > 0 and . == floor)' "$release_json")"; then
    cat -- "$release_json.create.log" "$release_json.create.error" >&2
    echo 'release draft creation response has no valid numeric id' >&2
    exit 1
  fi
  if ! check_draft; then
    replay_diagnostics
    echo 'created release draft does not match the exact empty-draft contract' >&2
    exit 1
  fi
  if ! get_release_by_id; then
    replay_diagnostics
    echo 'created release draft could not be read back by its numeric id' >&2
    exit 1
  fi
  if ! check_draft; then
    replay_diagnostics
    echo 'created release draft readback differs from the exact empty-draft contract' >&2
    exit 1
  fi
fi

if ! asset_count="$(jq -er '.assets|length' "$release_json")"; then
  replay_diagnostics
  echo 'release draft asset count could not be read from the API response' >&2
  exit 1
fi
if [[ "$asset_count" -eq 0 ]]; then
  test "$record_size" -le "$MAX_ASSET_BYTES"
  set +e
  timeout --signal=TERM --kill-after=5s 120s \
    gh api --include --hostname github.com --method POST \
      --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      --header 'Content-Type: application/octet-stream' --input "$RELEASE_RECORD" \
      "https://uploads.github.com/repos/$REPOSITORY/releases/$release_id/assets?name=$RELEASE_ASSET_NAME" &
  active_command_pid=$!
  wait "$active_command_pid"
  upload_status=$?
  active_command_pid=''
  set -e
  if [[ "$upload_status" -ne 0 ]]; then
    printf 'release asset upload failed (status %s)\n' "$upload_status" >&2
    exit 1
  fi
fi
if ! get_release_by_id; then
  replay_diagnostics
  echo 'release draft could not be read back after asset upload' >&2
  exit 1
fi
if ! check_draft; then
  replay_diagnostics
  echo 'release draft differs from the exact pre-publication contract' >&2
  exit 1
fi
if ! jq -e '(.assets | length) == 1' "$release_json" >/dev/null; then
  replay_diagnostics
  echo 'release draft does not contain the exact uploaded asset' >&2
  exit 1
fi
if ! asset_id="$(jq -er --arg asset "$RELEASE_ASSET_NAME" \
  '.assets | select(length == 1) | .[0] | select(.name == $asset) | .id | select(type == "number" and . > 0 and . == floor)' "$release_json")"; then
  replay_diagnostics
  echo 'release draft asset has no valid numeric id' >&2
  exit 1
fi
if ! capture_command "$downloaded_asset" "$release_error" 120 \
  gh api --hostname github.com --header 'Accept: application/octet-stream' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/assets/$asset_id"; then
  cat -- "$release_error" >&2
  echo 'release draft asset could not be downloaded for verification' >&2
  exit 1
fi
if ! test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES" \
  || ! cmp "$RELEASE_RECORD" "$downloaded_asset" \
  || ! test "$(wc -c <"$downloaded_asset")" = "$record_size" \
  || ! test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"; then
  replay_diagnostics
  echo 'downloaded draft asset differs from the exact release record' >&2
  exit 1
fi
set +e
timeout --signal=TERM --kill-after=5s 60s \
  gh api --include --hostname github.com --method PATCH \
    --header 'Accept: application/vnd.github+json' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    --field draft=false --field prerelease=false --field "name=$EXPECTED_TAG" \
    --field "body=$RELEASE_BODY" "repos/$REPOSITORY/releases/$release_id" &
active_command_pid=$!
wait "$active_command_pid"
edit_status=$?
active_command_pid=''
set -e
if ! get_release_by_id; then
  replay_diagnostics
  printf 'published release could not be read back after PATCH (status %s)\n' "$edit_status" >&2
  exit 1
fi
numeric_release_id="$release_id"
if ! jq -e --argjson release_id "$numeric_release_id" '.id == $release_id' "$release_json" >/dev/null; then
  replay_diagnostics
  echo 'published release readback has a different numeric id' >&2
  exit 1
fi

if ! env EXPECTED_TAG="$EXPECTED_TAG" RELEASE_ASSET_NAME="$RELEASE_ASSET_NAME" \
  RELEASE_BODY="$RELEASE_BODY" RECORD_DIGEST="$record_digest" RECORD_SIZE="$record_size" \
  PYTHONDONTWRITEBYTECODE=1 python3 .github/validate_release.py "$release_json"; then
  replay_diagnostics
  printf 'release publication failed after PATCH (status %s)\n' "$edit_status" >&2
  exit 1
fi
if [[ "$edit_status" -ne 0 ]]; then
  printf 'release PATCH transport returned status %s; exact immutable readback resolved the outcome\n' "$edit_status" >&2
fi
if ! asset_id="$(jq -er '.assets[0].id | select(type == "number" and . > 0 and . == floor)' "$release_json")" \
  || [[ ! "$asset_id" =~ ^[1-9][0-9]*$ ]]; then
  replay_diagnostics
  echo 'published release asset id is not a positive integer' >&2
  exit 1
fi
if ! capture_command "$downloaded_asset" "$release_error" 120 \
  gh api --hostname github.com --header 'Accept: application/octet-stream' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/assets/$asset_id"; then
  cat -- "$release_error" >&2
  echo 'immutable release asset could not be downloaded for final verification' >&2
  exit 1
fi
if ! test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES" \
  || ! cmp "$RELEASE_RECORD" "$downloaded_asset" \
  || ! test "$(wc -c <"$downloaded_asset")" = "$record_size" \
  || ! test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"; then
  replay_diagnostics
  echo 'immutable release asset differs from the exact release record' >&2
  exit 1
fi
