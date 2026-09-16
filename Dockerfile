# syntax=docker/dockerfile:1
# Build only in GitHub Actions. The server pulls an exact published tag; it never builds this image.
ARG SYNAPSE_BASE_REF=ghcr.io/element-hq/synapse:v0.0.0@sha256:0000000000000000000000000000000000000000000000000000000000000000
FROM ${SYNAPSE_BASE_REF} AS runtime

# The media behavior is carried by exact TeleCrypt fork source archives. The
# upstream base tags and fork commits are locked outside this Dockerfile and
# supplied only by the verified GitHub Actions inputs.
ARG SYNAPSE_BASE_REF
ARG SYNAPSE_VERSION
ARG SYNAPSE_BASE_DIGEST
ARG SYNAPSE_FORK_RELEASE
ARG SYNAPSE_FORK_COMMIT
ARG SYNAPSE_FORK_ARCHIVE_SHA256
ARG S3_PROVIDER_VERSION
ARG S3_PROVIDER_FORK_RELEASE
ARG S3_PROVIDER_FORK_COMMIT
ARG S3_PROVIDER_FORK_ARCHIVE_SHA256
ARG POLICY_RELEASE
ARG POLICY_WHEEL_SHA256
USER root
RUN --mount=type=bind,source=s3-provider.lock,target=/tmp/s3-provider.lock,readonly \
    --mount=type=bind,source=release-inputs,target=/tmp/release-inputs,readonly \
    set -eux; \
    test -n "${SYNAPSE_VERSION}" || { echo "SYNAPSE_VERSION is required" >&2; exit 2; }; \
    printf '%s\n' "${SYNAPSE_BASE_DIGEST}" | grep -Eq '^sha256:[0-9a-f]{64}$' || { echo "SYNAPSE_BASE_DIGEST is not an exact digest" >&2; exit 2; }; \
    test "${SYNAPSE_BASE_REF}" = "ghcr.io/element-hq/synapse:v${SYNAPSE_VERSION}@${SYNAPSE_BASE_DIGEST}" || { echo "SYNAPSE_BASE_REF does not match the exact version and digest" >&2; exit 2; }; \
    test -n "${SYNAPSE_FORK_RELEASE}" || { echo "SYNAPSE_FORK_RELEASE is required" >&2; exit 2; }; \
    printf '%s\n' "${SYNAPSE_FORK_RELEASE}" | grep -Eq '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-telecrypt\.[1-9][0-9]*$' || { echo "SYNAPSE_FORK_RELEASE is not an exact fork release" >&2; exit 2; }; \
    test -n "${SYNAPSE_FORK_COMMIT}" || { echo "SYNAPSE_FORK_COMMIT is required" >&2; exit 2; }; \
    printf '%s\n' "${SYNAPSE_FORK_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' || { echo "SYNAPSE_FORK_COMMIT is not an exact commit" >&2; exit 2; }; \
    test -n "${SYNAPSE_FORK_ARCHIVE_SHA256}" || { echo "SYNAPSE_FORK_ARCHIVE_SHA256 is required" >&2; exit 2; }; \
    test "${#SYNAPSE_FORK_ARCHIVE_SHA256}" -eq 64 || { echo "SYNAPSE_FORK_ARCHIVE_SHA256 must be 64 hex characters" >&2; exit 2; }; \
    case "${SYNAPSE_FORK_ARCHIVE_SHA256}" in *[!0123456789abcdef]*) echo "SYNAPSE_FORK_ARCHIVE_SHA256 must be lowercase hexadecimal" >&2; exit 2 ;; esac; \
    test -n "${S3_PROVIDER_VERSION}" || { echo "S3_PROVIDER_VERSION is required" >&2; exit 2; }; \
    test -n "${S3_PROVIDER_FORK_RELEASE}" || { echo "S3_PROVIDER_FORK_RELEASE is required" >&2; exit 2; }; \
    printf '%s\n' "${S3_PROVIDER_FORK_RELEASE}" | grep -Eq '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-telecrypt\.[1-9][0-9]*$' || { echo "S3_PROVIDER_FORK_RELEASE is not an exact fork release" >&2; exit 2; }; \
    test -n "${S3_PROVIDER_FORK_COMMIT}" || { echo "S3_PROVIDER_FORK_COMMIT is required" >&2; exit 2; }; \
    printf '%s\n' "${S3_PROVIDER_FORK_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' || { echo "S3_PROVIDER_FORK_COMMIT is not an exact commit" >&2; exit 2; }; \
    test -n "${S3_PROVIDER_FORK_ARCHIVE_SHA256}" || { echo "S3_PROVIDER_FORK_ARCHIVE_SHA256 is required" >&2; exit 2; }; \
    test "${#S3_PROVIDER_FORK_ARCHIVE_SHA256}" -eq 64 || { echo "S3_PROVIDER_FORK_ARCHIVE_SHA256 must be 64 hex characters" >&2; exit 2; }; \
    case "${S3_PROVIDER_FORK_ARCHIVE_SHA256}" in *[!0123456789abcdef]*) echo "S3_PROVIDER_FORK_ARCHIVE_SHA256 must be lowercase hexadecimal" >&2; exit 2 ;; esac; \
    test -n "${POLICY_RELEASE}" || { echo "POLICY_RELEASE is required" >&2; exit 2; }; \
    test -n "${POLICY_WHEEL_SHA256}" || { echo "POLICY_WHEEL_SHA256 is required" >&2; exit 2; }; \
    test "${#POLICY_WHEEL_SHA256}" -eq 64 || { echo "POLICY_WHEEL_SHA256 must be 64 hex characters" >&2; exit 2; }; \
    case "${POLICY_WHEEL_SHA256}" in *[!0123456789abcdef]*) echo "POLICY_WHEEL_SHA256 must be lowercase hexadecimal" >&2; exit 2 ;; esac; \
    synapse_archive="synapse-${SYNAPSE_FORK_RELEASE}.tar.gz"; \
    archive="synapse-s3-storage-provider-${S3_PROVIDER_FORK_RELEASE}.tar.gz"; \
    wheel="telecrypt_tier_controller-${POLICY_RELEASE}-py3-none-any.whl"; \
    synapse_archive_path="/tmp/release-inputs/${synapse_archive}"; \
    archive_path="/tmp/release-inputs/${archive}"; \
    wheel_path="/tmp/release-inputs/${wheel}"; \
    test -s "${synapse_archive_path}" || { echo "verified Synapse fork archive is missing" >&2; exit 2; }; \
    test -s "${archive_path}" || { echo "verified S3-provider fork archive is missing" >&2; exit 2; }; \
    test -s "${wheel_path}" || { echo "verified policy wheel is missing" >&2; exit 2; }; \
    printf '%s  %s\n' "${SYNAPSE_FORK_ARCHIVE_SHA256}" "${synapse_archive_path}" | sha256sum --strict --check -; \
    printf '%s  %s\n' "${S3_PROVIDER_FORK_ARCHIVE_SHA256}" "${archive_path}" | sha256sum --strict --check -; \
    printf '%s  %s\n' "${POLICY_WHEEL_SHA256}" "${wheel_path}" | sha256sum --strict --check -; \
    python3 -c 'import importlib.metadata as m; import setuptools.build_meta as backend; expected={"psycopg2":"2.9.11", "PyYAML":"6.0.3", "Twisted":"25.5.0", "python-dateutil":"2.9.0.post0", "six":"1.17.0", "urllib3":"2.7.0", "setuptools":"83.0.0"}; actual={name:m.version(name) for name in expected}; assert actual == expected, (expected, actual); assert backend.__name__ == "setuptools.build_meta", backend.__name__'; \
    printf '%s --hash=sha256:%s\n' "${archive_path}" "${S3_PROVIDER_FORK_ARCHIVE_SHA256}" > /tmp/s3-provider-artifacts.lock; \
    printf '%s --hash=sha256:%s\n' "${wheel_path}" "${POLICY_WHEEL_SHA256}" >> /tmp/s3-provider-artifacts.lock; \
    pip3 install \
      --disable-pip-version-check \
      --root-user-action=ignore \
      --no-cache-dir \
      --no-index \
      --find-links=/tmp/release-inputs/wheelhouse \
      --only-binary=:all: \
      --no-deps \
      --force-reinstall \
      --require-hashes \
      --requirement /tmp/s3-provider.lock; \
    pip3 install \
      --disable-pip-version-check \
      --root-user-action=ignore \
      --no-cache-dir \
      --no-index \
      --no-deps \
      --no-build-isolation \
      --force-reinstall \
      --require-hashes \
      --requirement /tmp/s3-provider-artifacts.lock; \
    mkdir -p /tmp/telecrypt-synapse-fork; \
    tar --extract --file="${synapse_archive_path}" --directory=/tmp/telecrypt-synapse-fork --strip-components=1 --no-same-owner --no-same-permissions; \
    synapse_site="$(python3 -c 'import importlib.util; spec=importlib.util.find_spec("synapse"); print(next(iter(spec.submodule_search_locations)) if spec and spec.submodule_search_locations else "")')"; \
    test -n "${synapse_site}"; \
    test -d /tmp/telecrypt-synapse-fork/synapse; \
    cp -a /tmp/telecrypt-synapse-fork/synapse/. "${synapse_site}/"; \
    rm -rf /tmp/telecrypt-synapse-fork; \
    pip3 check; \
    python3 -c 'import importlib.metadata as m, pathlib, sys; expected={}; [expected.__setitem__(line.split("==", 1)[0].lower().replace("_", "-"), line.split("==", 1)[1].split()[0]) for line in pathlib.Path("/tmp/s3-provider.lock").read_text().splitlines() if line and not line.startswith("#")]; expected.update({"matrix-synapse":sys.argv[1], "synapse-s3-storage-provider":sys.argv[2], "telecrypt-tier-controller":sys.argv[3]}); actual={name:m.version(name) for name in expected}; assert actual == expected, (expected, actual)' "${SYNAPSE_VERSION}" "${S3_PROVIDER_VERSION}" "${POLICY_RELEASE}"; \
    rm -f /tmp/s3-provider-artifacts.lock
COPY --chown=991:991 --chmod=0755 telecrypt-synapse-entrypoint /telecrypt-synapse-entrypoint
COPY --chown=991:991 LICENSE THIRD_PARTY_NOTICES.md /licenses/
USER 991:991

FROM runtime AS production
ENTRYPOINT ["/telecrypt-synapse-entrypoint"]
