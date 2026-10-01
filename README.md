# TeleCrypt Synapse server container

Reproducible TeleCrypt Synapse image builder. The image combines an exact upstream Synapse
release with the pinned TeleCrypt fork inputs and policy wheel selected by the version files.
Runtime configuration and credentials come from the deployment environment. See
[`LICENSE`](./LICENSE) and [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

Releases are prepared manually on an operator build host with Bash, Python 3, Docker with
Buildx, jq and the authenticated GitHub CLI. Target VMs only pull released images through
Salt. There are no GitHub Actions jobs. Helpers in `.github/` remain ordinary local tools.

Run the following blocks from the repository root in the same Bash session. Use a new
reviewed `TELECRYPT_REVISION` for each image release; never replace a published tag.
The source and annotated release tag must be committed and pushed before publication.

Validate and test locally (no dependency installation):

```bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
version_values=$(python3 .github/validate_versions.py versions.env)
provenance_values=$(python3 .github/validate_provenance.py provenance.lock versions.env)
eval "$version_values"
eval "$provenance_values"
python3 .github/validate_dependencies.py s3-provider.lock
for test_file in .github/test_*.py; do python3 "$test_file"; done
```

Prepare the locked inputs and build one image archive. `RELEASE_DIR` is retained until
publication succeeds, so a failed upload can reuse the tested archive. BuildKit's normal
local cache is reusable; no per-release dependency installation is needed on the host.

```bash
test -z "$(git status --porcelain)"
SOURCE_SHA=$(git rev-parse HEAD)
IMAGE=ghcr.io/telecrypt-io/telecrypt-synapse
RELEASE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/telecrypt-synapse-release.XXXXXX")
IMAGE_ARCHIVE="$RELEASE_DIR/telecrypt-synapse-$IMAGE_TAG.tar"
timeout --signal=TERM --kill-after=5s 12m python3 .github/prepare_inputs.py \
  --output release-inputs \
  --s3-provider-version "$S3_PROVIDER_VERSION" \
  --synapse-fork-release "$SYNAPSE_FORK_RELEASE" \
  --synapse-fork-commit "$SYNAPSE_FORK_COMMIT" \
  --synapse-fork-archive-sha256 "$SYNAPSE_FORK_ARCHIVE_SHA256" \
  --s3-provider-fork-release "$S3_PROVIDER_FORK_RELEASE" \
  --s3-provider-fork-commit "$S3_PROVIDER_FORK_COMMIT" \
  --s3-provider-fork-archive-sha256 "$S3_PROVIDER_FORK_ARCHIVE_SHA256" \
  --policy-release "$POLICY_RELEASE" \
  --policy-wheel-sha256 "$POLICY_WHEEL_SHA256"
python3 .github/validate_dependencies.py s3-provider.lock release-inputs/wheelhouse
BASE_REF="ghcr.io/element-hq/synapse:v$SYNAPSE_VERSION"
docker manifest inspect --verbose "$BASE_REF" > "$RELEASE_DIR/base.json"
BASE_DIGEST=$(jq -er '
  [ (if type == "array" then .[] else . end) | .Descriptor
    | select(.platform.os == "linux" and .platform.architecture == "amd64") | .digest ]
  | if length == 1 then .[0] else error("expected one amd64 base") end
  | select(test("^sha256:[0-9a-f]{64}$"))' "$RELEASE_DIR/base.json")
docker pull --platform linux/amd64 "$BASE_REF@$BASE_DIGEST"
BUILDX_METADATA_PROVENANCE=max docker buildx build \
  --target production --platform linux/amd64 --network none \
  --tag "$IMAGE:$IMAGE_TAG" --output "type=docker,dest=$IMAGE_ARCHIVE" \
  --metadata-file "$RELEASE_DIR/build.json" \
  --build-arg "SYNAPSE_BASE_REF=ghcr.io/element-hq/synapse:v${SYNAPSE_VERSION}@${BASE_DIGEST}" \
  --build-arg "SYNAPSE_VERSION=${SYNAPSE_VERSION}" \
  --build-arg "SYNAPSE_BASE_DIGEST=${BASE_DIGEST}" \
  --build-arg "SYNAPSE_FORK_RELEASE=${SYNAPSE_FORK_RELEASE}" \
  --build-arg "SYNAPSE_FORK_COMMIT=${SYNAPSE_FORK_COMMIT}" \
  --build-arg "SYNAPSE_FORK_ARCHIVE_SHA256=${SYNAPSE_FORK_ARCHIVE_SHA256}" \
  --build-arg "S3_PROVIDER_VERSION=${S3_PROVIDER_VERSION}" \
  --build-arg "S3_PROVIDER_FORK_RELEASE=${S3_PROVIDER_FORK_RELEASE}" \
  --build-arg "S3_PROVIDER_FORK_COMMIT=${S3_PROVIDER_FORK_COMMIT}" \
  --build-arg "S3_PROVIDER_FORK_ARCHIVE_SHA256=${S3_PROVIDER_FORK_ARCHIVE_SHA256}" \
  --build-arg "POLICY_RELEASE=${POLICY_RELEASE}" \
  --build-arg "POLICY_WHEEL_SHA256=${POLICY_WHEEL_SHA256}" \
  --label "org.opencontainers.image.licenses=BUSL-1.1" \
  --label "org.opencontainers.image.source=https://github.com/TeleCrypt-io/synapse-server" \
  --label "org.opencontainers.image.revision=${SOURCE_SHA}" \
  --label "org.opencontainers.image.version=${IMAGE_TAG}" \
  --label "org.opencontainers.image.base.name=ghcr.io/element-hq/synapse" \
  --label "org.opencontainers.image.base.version=v${SYNAPSE_VERSION}" \
  --label "org.opencontainers.image.base.digest=${BASE_DIGEST}" \
  --label "org.telecrypt.s3-provider.version=${S3_PROVIDER_VERSION}" \
  --label "org.telecrypt.s3-provider.upstream.commit=${S3_PROVIDER_UPSTREAM_COMMIT}" \
  --label "org.telecrypt.s3-provider.fork.release=${S3_PROVIDER_FORK_RELEASE}" \
  --label "org.telecrypt.s3-provider.fork.commit=${S3_PROVIDER_FORK_COMMIT}" \
  --label "org.telecrypt.s3-provider.fork.archive.sha256=${S3_PROVIDER_FORK_ARCHIVE_SHA256}" \
  --label "org.telecrypt.synapse.upstream.commit=${SYNAPSE_UPSTREAM_COMMIT}" \
  --label "org.telecrypt.synapse.fork.release=${SYNAPSE_FORK_RELEASE}" \
  --label "org.telecrypt.synapse.fork.commit=${SYNAPSE_FORK_COMMIT}" \
  --label "org.telecrypt.synapse.fork.archive.sha256=${SYNAPSE_FORK_ARCHIVE_SHA256}" \
  --label "org.telecrypt.controlplane.release=${POLICY_RELEASE}" \
  --label "org.telecrypt.controlplane.wheel.sha256=${POLICY_WHEEL_SHA256}" \
  .
python3 .github/verify_base_provenance.py "$RELEASE_DIR/build.json" "$BASE_REF" "$BASE_DIGEST"
image_contract_failures=0
check_image_value() {
  local description="$1" format="$2" expected="$3" actual
  if ! actual="$(docker image inspect "$IMAGE:$IMAGE_TAG" --format "$format")"; then
    printf 'image contract inspection failed (%s)\n' "$description" >&2
    image_contract_failures=$((image_contract_failures + 1))
    return
  fi
  if [[ "$actual" != "$expected" ]]; then
    printf 'image contract mismatch (%s): expected %q, got %q\n' \
      "$description" "$expected" "$actual" >&2
    image_contract_failures=$((image_contract_failures + 1))
  fi
}
test -s "$IMAGE_ARCHIVE"
test "$(stat -c '%s' "$IMAGE_ARCHIVE")" -le $((1024 * 1024 * 1024))
"$PWD/.github/load_image.sh" "$IMAGE_ARCHIVE"
check_image_value source '{{index .Config.Labels "org.opencontainers.image.source"}}' "https://github.com/TeleCrypt-io/synapse-server"
check_image_value license '{{index .Config.Labels "org.opencontainers.image.licenses"}}' "BUSL-1.1"
check_image_value version '{{index .Config.Labels "org.opencontainers.image.version"}}' "$IMAGE_TAG"
check_image_value revision '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$SOURCE_SHA"
check_image_value base-name '{{index .Config.Labels "org.opencontainers.image.base.name"}}' "ghcr.io/element-hq/synapse"
check_image_value base-version '{{index .Config.Labels "org.opencontainers.image.base.version"}}' "v${SYNAPSE_VERSION}"
check_image_value base-digest '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "${BASE_DIGEST}"
check_image_value user '{{.Config.User}}' "991:991"
check_image_value entrypoint '{{json .Config.Entrypoint}}' '["/telecrypt-synapse-entrypoint"]'
check_image_value s3-version '{{index .Config.Labels "org.telecrypt.s3-provider.version"}}' "${S3_PROVIDER_VERSION}"
check_image_value s3-upstream '{{index .Config.Labels "org.telecrypt.s3-provider.upstream.commit"}}' "${S3_PROVIDER_UPSTREAM_COMMIT}"
check_image_value s3-fork-release '{{index .Config.Labels "org.telecrypt.s3-provider.fork.release"}}' "${S3_PROVIDER_FORK_RELEASE}"
check_image_value s3-fork-commit '{{index .Config.Labels "org.telecrypt.s3-provider.fork.commit"}}' "${S3_PROVIDER_FORK_COMMIT}"
check_image_value s3-fork-archive '{{index .Config.Labels "org.telecrypt.s3-provider.fork.archive.sha256"}}' "${S3_PROVIDER_FORK_ARCHIVE_SHA256}"
check_image_value synapse-upstream '{{index .Config.Labels "org.telecrypt.synapse.upstream.commit"}}' "${SYNAPSE_UPSTREAM_COMMIT}"
check_image_value synapse-fork-release '{{index .Config.Labels "org.telecrypt.synapse.fork.release"}}' "${SYNAPSE_FORK_RELEASE}"
check_image_value synapse-fork-commit '{{index .Config.Labels "org.telecrypt.synapse.fork.commit"}}' "${SYNAPSE_FORK_COMMIT}"
check_image_value synapse-fork-archive '{{index .Config.Labels "org.telecrypt.synapse.fork.archive.sha256"}}' "${SYNAPSE_FORK_ARCHIVE_SHA256}"
check_image_value policy-release '{{index .Config.Labels "org.telecrypt.controlplane.release"}}' "${POLICY_RELEASE}"
check_image_value policy-wheel '{{index .Config.Labels "org.telecrypt.controlplane.wheel.sha256"}}' "${POLICY_WHEEL_SHA256}"
((image_contract_failures == 0))
timeout --signal=TERM --kill-after=5s 2m docker run --rm --network none \
  -v "$PWD/.github/image_smoke.py:/tmp/image_smoke.py:ro" \
  --env EXPECTED_SYNAPSE_VERSION="${SYNAPSE_VERSION}" \
  --env EXPECTED_S3_PROVIDER_VERSION="${S3_PROVIDER_VERSION}" \
  --env EXPECTED_SYNAPSE_FORK_RELEASE="${SYNAPSE_FORK_RELEASE}" \
  --env EXPECTED_SYNAPSE_FORK_COMMIT="${SYNAPSE_FORK_COMMIT}" \
  --env EXPECTED_S3_PROVIDER_FORK_RELEASE="${S3_PROVIDER_FORK_RELEASE}" \
  --env EXPECTED_S3_PROVIDER_FORK_COMMIT="${S3_PROVIDER_FORK_COMMIT}" \
  --env EXPECTED_POLICY_RELEASE="${POLICY_RELEASE}" \
  --entrypoint python "$IMAGE:$IMAGE_TAG" /tmp/image_smoke.py
TESTED_IMAGE_ID=$(docker image inspect "$IMAGE:$IMAGE_TAG" --format '{{.Id}}')
sha256sum "$IMAGE_ARCHIVE" > "$IMAGE_ARCHIVE.sha256"
printf 'Retained tested artifact: %s\n' "$RELEASE_DIR"
```

Publish the matching annotated tag once, after reviewing the tests. These commands
perform the registry and GitHub Release writes; the previous blocks only build and test.
Authenticate `docker login ghcr.io` with a package-write credential before this step.

```bash
git tag -a "$IMAGE_TAG" "$SOURCE_SHA" -m "TeleCrypt Synapse $IMAGE_TAG"
git push origin "refs/tags/$IMAGE_TAG"
git fetch origin main "refs/tags/$IMAGE_TAG"
test "$(git rev-parse origin/main)" = "$SOURCE_SHA"
test "$(git cat-file -t "refs/tags/$IMAGE_TAG")" = tag
test "$(git rev-parse "refs/tags/$IMAGE_TAG^{commit}")" = "$SOURCE_SHA"
ANNOTATED_TAG_SHA=$(git rev-parse "refs/tags/$IMAGE_TAG")
sha256sum --strict --check "$IMAGE_ARCHIVE.sha256"
bash .github/load_image.sh "$IMAGE_ARCHIVE"
test "$(docker image inspect "$IMAGE:$IMAGE_TAG" --format '{{.Id}}')" = "$TESTED_IMAGE_ID"
gh api --paginate --slurp \
  'orgs/TeleCrypt-io/packages/container/telecrypt-synapse/versions?per_page=100' \
  > "$RELEASE_DIR/package-versions.json"
classification=$(python3 .github/classify_registry_tag.py "$RELEASE_DIR/package-versions.json" "$IMAGE_TAG")
case "$classification" in
  absent)
    docker push "$IMAGE:$IMAGE_TAG"
    docker manifest inspect --verbose "$IMAGE:$IMAGE_TAG" > "$RELEASE_DIR/published.json"
    DIGEST=$(jq -er '
      (if type == "array" then
         if length == 1 then .[0] else error("expected one manifest") end
       else . end) | .Descriptor.digest
      | select(test("^sha256:[0-9a-f]{64}$"))' "$RELEASE_DIR/published.json")
    ;;
  existing\ sha256:*) DIGEST=${classification#existing } ;;
  *) echo "Invalid registry classification: $classification" >&2; exit 1 ;;
esac
bash .github/verify_registry_image.sh "$IMAGE:$IMAGE_TAG" "$TESTED_IMAGE_ID" "$DIGEST"
export RECORD_PATH="$RELEASE_DIR/telecrypt-synapse-$IMAGE_TAG.digest.json"
export RECORD_IMAGE="$IMAGE" RECORD_TAG="$IMAGE_TAG" RECORD_DIGEST="$DIGEST"
export RECORD_SOURCE_SHA="$SOURCE_SHA" RECORD_ANNOTATED_TAG_SHA="$ANNOTATED_TAG_SHA"
python3 .github/release_record.py
export GH_TOKEN="$(gh auth token)" GH_API_VERSION=2026-03-10
export GITHUB_REPOSITORY=TeleCrypt-io/synapse-server
export RELEASE_RECORD="$RECORD_PATH" RELEASE_ASSET_NAME="${RECORD_PATH##*/}"
export EXPECTED_TAG="$IMAGE_TAG" EXPECTED_SHA="$SOURCE_SHA"
export EXPECTED_ANNOTATED_TAG_SHA="$ANNOTATED_TAG_SHA" EXPECTED_DIGEST="$DIGEST"
bash .github/publish_release.sh
bash .github/verify_registry_image.sh "$IMAGE:$IMAGE_TAG" "$TESTED_IMAGE_ID" "$DIGEST"
```

On a retry, reuse the same session, archive and existing annotated tag; skip `git tag`
and `git push`. The registry verifier rejects any existing tag pointing to different
image content. The release helper verifies the uploaded digest record and immutable
release. The `server` repository owns the selected deployment versions and manifest;
verify the resulting deployment on Stage before owner-authorized Production promotion.

After publication and verification, remove only this release's local temporary artifacts:

```bash
rm -rf -- release-inputs "$RELEASE_DIR"
```
