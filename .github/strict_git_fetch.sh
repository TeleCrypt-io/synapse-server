#!/usr/bin/env bash
set -euo pipefail

# Release checks use one fixed HTTPS repository and a trusted runner Git. Keep
# the boundary small: reject options/refspecs, clear ambient transport state,
# and let Git validate the checkout it is operating on.
readonly GIT=/usr/bin/git
readonly REPOSITORY='TeleCrypt-io/synapse-server-container'
readonly REMOTE='https://github.com/TeleCrypt-io/synapse-server-container.git'
readonly REMOTE_WITHOUT_SUFFIX='https://github.com/TeleCrypt-io/synapse-server-container'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/telecrypt-git.XXXXXX")"
readonly TEMP_ROOT
cleanup() {
  local cleanup_status=0
  if ! rm -rf -- "$TEMP_ROOT"; then
    echo 'ERROR: could not remove Git transport diagnostics' >&2
    cleanup_status=1
  fi
  return "$cleanup_status"
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
        printf 'ERROR: Git transport child termination failed during %s (status %s)\n' "$signal_name" "$kill_status" >&2
      fi
    elif [[ "$probe_status" -ne 1 ]]; then
      printf 'ERROR: Git transport child liveness check failed during %s (status %s)\n' "$signal_name" "$probe_status" >&2
    fi
    wait "$active_command_pid"
    wait_status=$?
    if [[ "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" ]]; then
      printf 'ERROR: Git transport child wait failed during %s (status %s)\n' "$signal_name" "$wait_status" >&2
    fi
    active_command_pid=''
  fi
  if [[ -d "$TEMP_ROOT" ]]; then
    while IFS= read -r -d '' path; do
      cat -- "$path" >&2
      if [[ "$?" -ne 0 ]]; then
        echo "ERROR: could not replay Git transport diagnostics: $path" >&2
        replay_status=1
      fi
    done < <(find "$TEMP_ROOT" -type f -print0)
  fi
  cleanup_status=0
  cleanup || cleanup_status=$?
  if [[ "$probe_status" -gt 1 || "$kill_status" -ne 0 || "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" || "$replay_status" -ne 0 || "$cleanup_status" -ne 0 ]]; then
    exit_status=1
  fi
  exit "$exit_status"
}
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

die() { printf 'git transport refused: %s\n' "$1" >&2; exit 64; }

[[ -x "$GIT" ]] || die 'trusted Git executable is unavailable'
export PATH=/usr/bin:/bin
for variable in ${!GIT_CONFIG_KEY_@} ${!GIT_CONFIG_VALUE_@}; do
  unset "$variable"
done
unset GIT_CONFIG_PARAMETERS GIT_DIR GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE GIT_NAMESPACE GIT_REPLACE_REF_BASE \
  GIT_QUARANTINE_PATH GIT_WORK_TREE GIT_CEILING_DIRECTORIES \
  GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_EXEC_PATH GIT_TEMPLATE_DIR GIT_CONFIG \
  GIT_OPTIONAL_LOCKS GIT_SSH GIT_SSH_COMMAND GIT_SSH_VARIANT GIT_PROXY_COMMAND \
  GIT_EXT_SERVICE GIT_EXT_SERVICE_NOPREFIX
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_COUNT=0 GIT_NO_REPLACE_OBJECTS=1 GIT_TERMINAL_PROMPT=0
unset GIT_ASKPASS SSH_ASKPASS HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
  http_proxy https_proxy all_proxy no_proxy GIT_HTTP_PROXY_AUTHMETHOD \
  GIT_SSL_NO_VERIFY GIT_SSL_CAFILE GIT_SSL_CAINFO GIT_SSL_CAPATH GIT_SSL_CERT \
  GIT_SSL_KEY GIT_SSL_CIPHER_LIST GIT_SSL_VERSION SSL_CERT_FILE SSL_CERT_DIR \
  CURL_CA_BUNDLE REQUESTS_CA_BUNDLE GIT_ALLOW_PROTOCOL GIT_PROTOCOL_FROM_USER \
  GIT_TRACE GIT_TRACE_PACK_ACCESS GIT_TRACE_PERFORMANCE GIT_TRACE_SETUP \
  GIT_TRACE_PACKET GIT_TRACE_SHALLOW GIT_TRACE_CURL GIT_CURL_VERBOSE GIT_TRACE2 \
  GIT_TRACE2_EVENT GIT_TRACE2_PERF GIT_TRACE2_BRIEF GIT_TRACE2_CONFIG \
  GIT_TRACE2_ENV_VARS GIT_TRACE2_MAX_FILES GIT_TRACE2_PARENT GIT_TRACE2_PERF_BRIEF \
  GIT_TRACE_REDACT

readonly GIT_OPTIONS=(
  -c protocol.version=2
  -c protocol.allow=never
  -c protocol.https.allow=always
  -c protocol.file.allow=never
  -c protocol.ext.allow=never
  -c credential.helper=
  -c credential.useHttpPath=false
  -c credential.interactive=false
  -c http.proxy=
  -c http.sslVerify=true
  -c core.sshCommand=
  -c core.gitproxy=
  -c core.hooksPath=/dev/null
  -c remote.origin.proxy=
  -c remote.origin.uploadpack=
)

git_safe() {
  local stdout_file stderr_file status
  stdout_file="$(mktemp "$TEMP_ROOT/stdout.XXXXXX")"
  stderr_file="$(mktemp "$TEMP_ROOT/stderr.XXXXXX")"
  set +e
  timeout --signal=TERM --kill-after=5s 30s \
    "$GIT" "${GIT_OPTIONS[@]}" "$@" >"$stdout_file" 2>"$stderr_file" &
  active_command_pid=$!
  wait "$active_command_pid"
  status=$?
  active_command_pid=''
  set -e
  cat -- "$stdout_file"
  cat -- "$stderr_file" >&2
  rm -f -- "$stdout_file" "$stderr_file"
  return "$status"
}

reject_local_transport_config() {
  local git_dir config_file config_worktree
  [[ ! -L "$PWD/.git" ]] || die 'repository metadata must not be a symlink'
  git_dir="$(git_safe rev-parse --git-dir)"
  [[ -d "$git_dir" && ! -L "$git_dir" ]] || die 'repository Git directory is invalid'
  config_file="$git_dir/config"
  inspect_config_file "$config_file"
  config_worktree="$git_dir/config.worktree"
  if [[ -L "$config_worktree" || -e "$config_worktree" ]]; then
    inspect_config_file "$config_worktree"
  fi
}

inspect_config_file() {
  local config_file="$1" keys remote_keys origin status key
  [[ -f "$config_file" && ! -L "$config_file" ]] || die 'Git configuration is not a regular file'
  [[ "$(stat -c '%s' -- "$config_file")" -le 65536 ]] || die 'Git configuration exceeds the bounded input limit'

  set +e
  keys="$(git_safe config --file "$config_file" --no-includes --name-only --get-regexp \
    '^(include(if)?\.|url(\..*)?$|credential(\..*)?$|hooks(\.|$)|core\.(askpass|ssh.*|gitproxy|hookspath|worktree|alternateobjectdirectories)$|http(\..*)?$|protocol(\..*)?$|ssh\..*$|fetch\..*$|transfer\..*$|uploadpack\..*$|receivepack\..*$|remote\..*\.(pushurl|vcs|uploadpack|proxy|receivepack)$)')"
  status=$?
  set -e
  [[ "$status" -eq 0 || "$status" -eq 1 ]] || die 'Git configuration could not be inspected'
  [[ -z "$keys" ]] || die 'Git transport, credential, or hook configuration is not allowed'

  set +e
  remote_keys="$(git_safe config --file "$config_file" --no-includes --name-only --get-regexp '^remote\..*\.url$')"
  status=$?
  set -e
  [[ "$status" -eq 0 || "$status" -eq 1 ]] || die 'Git remote configuration could not be inspected'
  while IFS= read -r key; do
    [[ -z "$key" || "$key" == remote.origin.url ]] || die 'non-canonical Git remote URL is not allowed'
  done <<<"$remote_keys"

  set +e
  origin="$(git_safe config --file "$config_file" --no-includes --get-all remote.origin.url)"
  status=$?
  set -e
  [[ "$status" -eq 0 || "$status" -eq 1 ]] || die 'Git origin could not be inspected'
  if [[ -n "$origin" ]]; then
    [[ "$origin" == "$REMOTE" || "$origin" == "$REMOTE_WITHOUT_SUFFIX" ]] ||
      die 'local Git origin is not canonical'
  fi
}

validate_ref() {
  [[ "$1" =~ ^(HEAD|[0-9a-f]{40}|refs/(tags|remotes/origin)/[A-Za-z0-9._/-]+(\^\{commit\}|\^\{\})?)$ ]] ||
    die 'local Git reference is malformed'
}

validate_tag() {
  [[ "$1" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-tc[1-9][0-9]*$ ]] || die 'release tag is not canonical'
}

validate_refspec() {
  [[ "$1" =~ ^refs/(heads/main|tags/[A-Za-z0-9._/-]+):refs/(remotes/origin/[A-Za-z0-9._/-]+|tags/[A-Za-z0-9._/-]+)$ ]] ||
    die 'Git fetch refspec is malformed'
  if [[ "$1" =~ ^refs/tags/([^:]+): ]]; then validate_tag "${BASH_REMATCH[1]}"; fi
}

local_read() {
  local kind="$1" ref="$2"
  validate_ref "$ref"
  case "$kind" in
    cat-file-type) git_safe cat-file -t -- "$ref" ;;
    rev-parse) git_safe rev-parse --verify "$ref" ;;
    *) die 'unsupported local Git read' ;;
  esac
}

case "${1:-}" in
  check)
    [[ $# -eq 1 ]] || die 'check takes no arguments'
    reject_local_transport_config
    git_safe rev-parse --git-dir >/dev/null
    ;;
  local-read)
    [[ $# -eq 3 ]] || die 'local-read requires a read kind and reference'
    reject_local_transport_config
    local_read "$2" "$3"
    ;;
  local-ancestor)
    [[ $# -eq 3 ]] || die 'local-ancestor requires two references'
    reject_local_transport_config
    validate_ref "$2"; validate_ref "$3"
    git_safe merge-base --is-ancestor "$2" "$3"
    ;;
  fetch)
    shift
    if [[ "${1:-}" == "$REPOSITORY" ]]; then shift; fi
    [[ $# -ge 1 ]] || die 'fetch requires a fixed refspec'
    reject_local_transport_config
    for refspec; do validate_refspec "$refspec"; done
    git_safe fetch --force --no-tags "$REMOTE" "$@"
    ;;
  ls-remote)
    shift
    if [[ "${1:-}" == "$REPOSITORY" ]]; then shift; fi
    [[ $# -ge 1 ]] || die 'ls-remote requires a fixed remote reference'
    reject_local_transport_config
    for ref; do
      [[ "$ref" =~ ^refs/tags/([A-Za-z0-9._/-]+)$ ]] || die 'Git remote reference is malformed'
      validate_tag "${BASH_REMATCH[1]}"
    done
    git_safe ls-remote --exit-code "$REMOTE" "$@"
    ;;
  refs/*)
    reject_local_transport_config
    for refspec; do validate_refspec "$refspec"; done
    git_safe fetch --force --no-tags "$REMOTE" "$@"
    ;;
  *) die 'unsupported operation' ;;
esac
