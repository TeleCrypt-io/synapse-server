# TeleCrypt Synapse server container

Reproducible TeleCrypt Synapse image builder. The image combines an exact upstream Synapse
release with the pinned TeleCrypt fork inputs and policy wheel selected by the version files.
Runtime configuration and credentials come from the deployment environment. See
[`LICENSE`](./LICENSE) and [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

GitHub Actions validates pull requests and builds the image from the reviewed source. A push
to `main` runs the same checks and build. Publication starts only when an annotated tag matching
the image version is pushed, and the workflow verifies that the tag points to the current
`main` commit before publishing the immutable image and GitHub Release. Target VMs pull the
published image through Salt; they do not build it. Matrix Salt Pillar selects the Synapse
image version for deployment.

Prepare releases by reviewing changes to `versions.env`, `provenance.lock`, and
`s3-provider.lock`, then merge them to `main`. The image tag is derived from `SYNAPSE_VERSION`
and `TELECRYPT_REVISION` in `versions.env`; for example, `1.159.0` and `34` produce
`1.159-tc34`. Push that matching annotated tag on the merged commit to publish.

Run the local offline validation and behavior tests from the repository root:

```bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
python3 .github/validate_versions.py versions.env
python3 .github/validate_provenance.py provenance.lock versions.env
python3 .github/validate_dependencies.py s3-provider.lock
for test_file in .github/test_*.py; do python3 "$test_file"; done
```
