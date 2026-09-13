# TeleCrypt Synapse

Public, reproducible Synapse image for TeleCrypt.

Current TeleCrypt project facts and architecture decisions are maintained only in the canonical
[`llms.txt`](https://telecrypt-io.github.io/llms-authority/llms.txt); this README documents the image build and release.

```text
official Synapse release
  + exact TeleCrypt Synapse fork source archive
  + exact TeleCrypt S3-provider fork source archive
  + exact external TeleCrypt tier-controller release
  = ghcr.io/telecrypt-io/telecrypt-synapse:<version>
```

This repository starts from the official exact Synapse image and overlays the Python package from
the exact TeleCrypt Synapse fork archive. It installs the exact TeleCrypt S3-provider fork archive
and the external TeleCrypt tier-controller wheel. The TeleCrypt module limits unverified users'
uploads, encrypted room creation, encryption events, and room count; only Synapse users with
`user_type: verified` are unrestricted.

## Image versions

An image tag has the canonical form `<major>.<minor>-tc<positive-revision>`, with no leading
zeros in any numeric component.

- `<major>.<minor>` identifies the upstream Element Synapse release line.
- `tc<positive-revision>` identifies the intended TeleCrypt build for that line. A publication
  accepts an existing GHCR tag only when it proves the exact tested image identity and digest;
  otherwise it fails closed.

The checked-in `versions.env` owns exact upstream versions and the Controlplane release selector.
The checked-in `provenance.lock` owns the corresponding fork releases and commits, source-archive
digests, and Controlplane wheel digest.
Before downloading either fork archive,
the workflow fetches the exact GitHub Release by tag and requires `draft: false`, `prerelease: false`,
explicit `immutable: true`, zero uploaded assets, and an annotated tag that peels to the locked fork
commit. It then downloads the published GitHub tag archive itself and compares its bytes with the
locked SHA-256; a locally produced archive is never accepted as a substitute. The workflow strictly
validates both files for every pull request, main-branch push, and exact release-tag push, then passes
their values to the Dockerfile and OCI labels. The Dockerfile has no version defaults, so changing a
component requires a reviewed source change.

The provider fork's dependencies are installed from the reviewed `s3-provider.lock` file with exact
versions and hashes. GitHub Actions prepares and verifies an exact binary wheelhouse, the provider
source archive, and the Controlplane wheel before the Docker build; the Controlplane release API
metadata must identify the exact immutable non-prerelease release and exactly the wheel plus
`controlplane-<release>.digest.json`. That asset is the compact canonical six-key Controlplane
image record; its source/tag identity is matched to the independently fetched annotated tag object
and peeled commit. The configured wheel digest, size, and release URL are checked separately against
the API and downloaded wheel bytes. The Dockerfile then installs
only from those local inputs with the build `RUN` network disabled. Dependencies already present in
the exact Synapse base are checked against their expected versions, including the setuptools build
backend required by the provider's setup.py. The provider archive is rejected during preparation if
it changes to an unreviewed pyproject build contract; dependency resolution and build isolation are
disabled. BuildKit mounts the verified lock and release inputs read-only for this install step, so
they do not survive in the final image layers.

Images are built only by this repository's GitHub Actions workflows. Pull requests run the exact
candidate smoke test, as do main-branch pushes; publication is allowed only from the matching
annotated Git tag after that test passes. There is no scheduled publisher or automatic release
discovery. The policy unit suite belongs to Controlplane. This workflow never deploys to TeleCrypt
infrastructure, and a failed candidate is not published. Checkout-only credentials are removed before
strict Git checks; only Git's documented no-match statuses are treated as absent. Required Git
fetches, Docker pulls and loads, and package downloads retain their normal progress, warning, and
error output.

GitHub Release publication is draft-first and exact. An authenticated lookup by exact tag
recovers an interrupted create, and a draft with no asset or a same-name interrupted upload is
repaired from the already-tested record. Unexpected or ambiguous assets fail closed. A pre-existing
published Release is refused; only the same exact draft may be resumed.
For the image itself, an existing GHCR tag may resume only after the linux/amd64 child has the exact
tested image identity, a single leaf image-manifest descriptor, and a matching digest; otherwise it
fails closed. A new image tag is checked absent immediately before its one push. The workflow
refreshes and rechecks the annotated source tag and exact current `main` commit before each publication
boundary and once after Release publication. GHCR has no documented immutable-tag or atomic
create-if-absent operation that this workflow can require. It therefore performs the final absence
check immediately before the one push, then fails closed unless the pushed tag resolves to the exact
tested image; a writer racing that narrow check cannot be prevented or proven absent by the client.

Before creating a source release tag, the repository operator must enable GitHub's immutable-Releases
setting and verify it through the operator/Harness control plane; the Actions token cannot read that
administrative setting. After the exact image is pushed, Actions creates one non-prerelease GitHub
Release with the exact tag and a deterministic `telecrypt-synapse-<tag>.digest.json` asset binding
the image, manifest digest, source commit, and annotated tag object. It downloads and byte-verifies
that asset, then requires the final Release to report `immutable: true`. The digest is durable
identity evidence, and the immutable Release is the source-tag lock; consumers continue to deploy
the exact version tag, not a digest coordinate.

`server_state` is responsible only for selecting a tested exact image tag in a separately released
`server-state-*` configuration change. The Linux VM must never build or install Python packages at
runtime.

## Components

- Base: `ghcr.io/element-hq/synapse:v<version>`, with the upstream commit recorded in `provenance.lock`.
- Media provider: the TeleCrypt fork of [`matrix-org/synapse-s3-storage-provider`](https://github.com/TeleCrypt-io/synapse-s3-storage-provider), preserving its Apache-2.0 license and pinned by immutable fork release, commit, and source-archive hash in `provenance.lock`.
- Policy: [`TeleCrypt-io/controlplane`](https://github.com/TeleCrypt-io/controlplane), installed as a
  `telecrypt_tier_controller` wheel from the exact public GitHub Release selected in `versions.env`.
  Its exact SHA-256 digest is recorded in `provenance.lock` and verified before the offline image build.

The upstream Synapse base remains an exact release version rather than a digest-pinned setting. Each
build records the current linux/amd64 manifest digest for that version tag in the image's OCI base
digest label and verifies the BuildKit provenance material against that digest. A changed upstream
tag is therefore detected during the build and the produced image remains independently identifiable.
The fork release, commit, and source archive identity are recorded in OCI labels and checked before
image publication; the build fails closed if the immutable source-only release is unavailable or
either exact archive hash differs.

The provider is configured by Synapse's `media_storage_providers` setting; this image contains no
S3 endpoint, bucket, or credentials. Those remain server-only secrets.

The image has one `/telecrypt-synapse-entrypoint`. Before Synapse starts it creates and clears the
disposable `/staging/tmp` and `/staging/media` directories, sets `TMPDIR=/staging/tmp`, and
executes the single Synapse homeserver process. Symlink entries are unlinked without following their
targets, and the staging root itself is left intact.

## Release and deployment boundary

A published image is an available, tested artifact—not a deployment. To adopt one:

1. create a reviewed immutable `server-state-*` release referencing its exact tag;
2. verify the exact release identity and deploy that `server_state` release to stage;
3. run private operator-local Harness acceptance against the deployed stage release; and
4. promote the identical tested release only after acceptance passes and the owner instructs it.

Never use `latest`, a floating Synapse tag, a bind-mounted Python module, or a runtime `pip install`.

## Licensing

TeleCrypt-authored source is BUSL-1.1. The image also includes third-party components under their
own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
