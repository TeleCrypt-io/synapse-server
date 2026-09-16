# Third-party notices

This project packages, but does not modify, the following third-party components:

- [Synapse](https://github.com/element-hq/synapse), supplied by its official container image at
  the exact version recorded in `versions.env` and licensed by Element under its own terms.
- The provider's compatible build and base-image dependencies (including setuptools, psycopg2,
  PyYAML, Twisted, python-dateutil, six, and urllib3) remain governed by the exact Synapse base
  image's notices; this repository does not download replacement copies of those packages.
- [synapse-s3-storage-provider](https://github.com/TeleCrypt-io/fork-synapse-s3-storage-provider),
  Apache License 2.0, pinned to the exact released version in `versions.env`.
- [control-plane tier controller](https://github.com/TeleCrypt-io/control-plane), TeleCrypt BUSL-1.1
  code, installed from the exact public release recorded in `versions.env`.
- [boto3](https://pypi.org/project/boto3/1.43.78/), Apache-2.0, exact wheel recorded in
  `s3-provider.lock`.
- [botocore](https://pypi.org/project/botocore/1.43.78/), Apache-2.0, exact wheel recorded in
  `s3-provider.lock`.
- [humanize](https://pypi.org/project/humanize/4.16.0/), MIT, exact wheel recorded in
  `s3-provider.lock`.
- [jmespath](https://pypi.org/project/jmespath/1.1.0/), MIT, exact wheel recorded in
  `s3-provider.lock`.
- [s3transfer](https://pypi.org/project/s3transfer/0.19.2/), Apache-2.0, exact wheel recorded in
  `s3-provider.lock`.
- [tqdm](https://pypi.org/project/tqdm/4.70.0/), MPL-2.0 and MIT, exact wheel recorded in
  `s3-provider.lock`.

The exact package metadata and license expressions are recorded by the linked PyPI release pages.
The published image must retain all notices and license obligations from these dependencies.
