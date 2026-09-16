#!/usr/bin/env python3
"""Focused offline tests for the policy release boundary."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from urllib.error import HTTPError
import urllib.request
from unittest import mock

import prepare_inputs
import validate_release
import verify_base_provenance


RELEASE = "0.4.0"
WHEEL = f"telecrypt_tier_controller-{RELEASE}-py3-none-any.whl"
DIGEST_ASSET = f"controlplane-{RELEASE}.digest.json"
WHEEL_SHA = "f" * 64
ANNOTATED_TAG_SHA = "a" * 40
SOURCE_COMMIT = "b" * 40
FORK_REPOSITORY = "TeleCrypt-io/fork-synapse"
FORK_RELEASE = "v1.159.0-telecrypt.1"
FORK_ANNOTATED_TAG_SHA = "d" * 40
FORK_SOURCE_COMMIT = "e" * 40


def record(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "annotated_tag_sha": ANNOTATED_TAG_SHA,
        "digest": "sha256:" + "c" * 64,
        "image": prepare_inputs.POLICY_IMAGE,
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "tag": RELEASE,
    }
    payload.update(changes)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def synapse_record(tag: str = "1.159-tc3") -> bytes:
    payload = {
        "annotated_tag_sha": ANNOTATED_TAG_SHA,
        "digest": "sha256:" + "c" * 64,
        "image": "ghcr.io/telecrypt-io/telecrypt-synapse",
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "tag": tag,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def asset(
    name: str, asset_id: int, size: int, digest: str, release: str = RELEASE
) -> dict[str, object]:
    return {
        "id": asset_id,
        "name": name,
        "size": size,
        "digest": f"sha256:{digest}",
        "label": "",
        "state": "uploaded",
        "created_at": "2026-08-22T00:00:01Z",
        "updated_at": "2026-08-22T00:00:02Z",
        "url": (
            f"https://api.github.com/repos/TeleCrypt-io/control-plane/releases/assets/{asset_id}"
        ),
        "browser_download_url": (
            f"https://github.com/TeleCrypt-io/control-plane/releases/download/{release}/{name}"
        ),
    }


def release_metadata(release: str = RELEASE) -> dict[str, object]:
    wheel = f"telecrypt_tier_controller-{release}-py3-none-any.whl"
    digest_asset = f"controlplane-{release}.digest.json"
    return {
        "id": 42,
        "tag_name": release,
        "name": release,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "body": f"Exact Controlplane release {release}.",
        "created_at": "2026-08-22T00:00:00Z",
        "published_at": "2026-08-23T00:00:00Z",
        "url": "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/42",
        "assets_url": "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/42/assets",
        "upload_url": "https://uploads.github.com/repos/TeleCrypt-io/control-plane/releases/42/assets{?name,label}",
        "html_url": f"https://github.com/TeleCrypt-io/control-plane/releases/tag/{release}",
        "tarball_url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/tarball/{release}",
        "zipball_url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/zipball/{release}",
        "assets": [
            asset(wheel, 1, 7, WHEEL_SHA, release),
            asset(digest_asset, 2, 128, "0" * 64, release),
        ],
    }


def fork_release_metadata(
    repository: str = FORK_REPOSITORY,
    release: str = FORK_RELEASE,
) -> dict[str, object]:
    return {
        "id": 99,
        "tag_name": release,
        "name": release,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "url": f"https://api.github.com/repos/{repository}/releases/99",
        "assets_url": f"https://api.github.com/repos/{repository}/releases/99/assets",
        "upload_url": f"https://uploads.github.com/repos/{repository}/releases/99/assets{{?name,label}}",
        "html_url": f"https://github.com/{repository}/releases/tag/{release}",
        "tarball_url": f"https://api.github.com/repos/{repository}/tarball/{release}",
        "zipball_url": f"https://api.github.com/repos/{repository}/zipball/{release}",
        "assets": [],
    }


class PrepareInputsTests(unittest.TestCase):
    def test_download_uses_explicit_host_specific_media_types(self) -> None:
        payload = b"exact archive bytes"
        expected = hashlib.sha256(payload).hexdigest()

        class Response:
            def __init__(self) -> None:
                self.remaining = [payload, b""]

            def __enter__(self):
                return self

            def __exit__(self, *unused: object) -> None:
                return None

            def read(self, unused_size: int) -> bytes:
                return self.remaining.pop(0)

        requests: list[urllib.request.Request] = []

        def open_request(request: urllib.request.Request, *, timeout: int) -> Response:
            self.assertEqual(timeout, prepare_inputs.DOWNLOAD_TIMEOUT_SECONDS)
            requests.append(request)
            return Response()

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            prepare_inputs.URL_OPENER, "open", side_effect=open_request
        ):
            root = Path(directory)
            prepare_inputs.download(
                "https://api.github.com/repos/TeleCrypt-io/fork-synapse/tarball/v1.0.0-telecrypt.1",
                root / "archive.tar.gz",
                expected,
                expected_host="api.github.com",
                accept=prepare_inputs.GITHUB_API_ACCEPT,
            )
            prepare_inputs.download(
                "https://github.com/TeleCrypt-io/control-plane/releases/download/1.0.0/input.whl",
                root / "input.whl",
                expected,
            )
        self.assertEqual(
            [request.get_header("Accept") for request in requests],
            [prepare_inputs.GITHUB_API_ACCEPT, prepare_inputs.BINARY_ACCEPT],
        )

    def test_download_retains_failed_temporary_input(self) -> None:
        payload = b"input that failed verification"

        class Response:
            def __init__(self) -> None:
                self.remaining = [payload, b""]

            def __enter__(self):
                return self

            def __exit__(self, *unused: object) -> None:
                return None

            def read(self, unused_size: int) -> bytes:
                return self.remaining.pop(0)

        with tempfile.TemporaryDirectory(delete=False, prefix="prepare-inputs-download-") as directory:
            root = Path(directory)
            with mock.patch.object(
                prepare_inputs.URL_OPENER, "open", return_value=Response()
            ), self.assertRaises(SystemExit) as failure:
                prepare_inputs.download(
                    "https://github.com/TeleCrypt-io/fork-synapse/archive.tar.gz",
                    root / "archive.tar.gz",
                    "0" * 64,
                )

            message = str(failure.exception)
            marker = "retained temporary input: "
            self.assertIn(marker, message)
            retained = Path(message.split(marker, 1)[1])
            self.assertTrue(retained.exists())
            self.assertEqual(retained.read_bytes(), payload)

    def test_github_api_http_error_retains_read_and_close_failures(self) -> None:
        class FailingBody:
            def __init__(self) -> None:
                self.closed = False

            def read(self) -> bytes:
                raise IncompleteRead(b"partial response body", 99)

            def close(self) -> None:
                self.closed = True
                raise OSError("fixture response close failed")

        body = FailingBody()
        error = HTTPError(
            "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/tags/0.4.0",
            503,
            "service unavailable",
            hdrs=None,
            fp=body,
        )
        with mock.patch.object(prepare_inputs.URL_OPENER, "open", side_effect=error):
            with self.assertRaises(SystemExit) as failure:
                prepare_inputs.fetch_github_api(
                    "TeleCrypt-io/control-plane", "releases/tags/0.4.0", "Controlplane"
                )
        self.assertTrue(body.closed)
        message = str(failure.exception)
        self.assertIn("503", message)
        self.assertIn("partial response body", message)
        self.assertIn("response read failed: IncompleteRead", message)
        self.assertIn("response close failed: fixture response close failed", message)

    def test_pip_accepts_silent_success(self) -> None:
        prepare_inputs.run_pip([sys.executable, "-c", "pass"])

    def test_pip_surfaces_failure_diagnostics(self) -> None:
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics), self.assertRaises(SystemExit) as failure:
            prepare_inputs.run_pip(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('fixture failure', file=sys.stderr); raise SystemExit(2)",
                ]
            )
        self.assertIn("fixture failure", diagnostics.getvalue())
        self.assertIn("exit code 2", str(failure.exception))

    def test_pip_preserves_success_diagnostics(self) -> None:
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            prepare_inputs.run_pip([sys.executable, "-c", "print('normal output')"])
        self.assertIn("normal output", diagnostics.getvalue())

    def test_github_api_preserves_http_error_body(self) -> None:
        class TrackingBody(io.BytesIO):
            def __init__(self, value: bytes) -> None:
                super().__init__(value)
                self.closed_by_fetch = False

            def close(self) -> None:
                self.closed_by_fetch = True
                super().close()

        response_body = TrackingBody(b"github API failure body")
        error = HTTPError(
            "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/tags/0.4.0",
            503,
            "service unavailable",
            hdrs=None,
            fp=response_body,
        )
        with mock.patch.object(prepare_inputs.URL_OPENER, "open", side_effect=error):
            with self.assertRaises(SystemExit) as failure:
                prepare_inputs.fetch_github_api(
                    "TeleCrypt-io/control-plane", "releases/tags/0.4.0", "Controlplane"
                )
        self.assertTrue(response_body.closed_by_fetch)
        self.assertIn("github API failure body", str(failure.exception))

    def test_github_api_preserves_malformed_body(self) -> None:
        url = "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/tags/0.4.0"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = None
        response.geturl.return_value = url
        response.read.return_value = b'{"release":'
        with mock.patch.object(prepare_inputs.URL_OPENER, "open", return_value=response):
            with self.assertRaises(SystemExit) as failure:
                prepare_inputs.fetch_github_api(
                    "TeleCrypt-io/control-plane", "releases/tags/0.4.0", "Controlplane"
                )
        self.assertIn('{"release":', str(failure.exception))

    def run_publish_release(
        self, record_bytes: bytes, **changes: str
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "GH_TOKEN": "offline-test-token",
            "GH_API_VERSION": "2026-03-10",
            "GITHUB_REPOSITORY": "TeleCrypt-io/synapse-server",
            "RELEASE_ASSET_NAME": f"telecrypt-synapse-{RELEASE}.digest.json",
            "EXPECTED_TAG": "1.159-tc3",
            "EXPECTED_SHA": SOURCE_COMMIT,
            "EXPECTED_ANNOTATED_TAG_SHA": ANNOTATED_TAG_SHA,
            "EXPECTED_DIGEST": "sha256:" + "c" * 64,
        }
        environment.update(changes)
        with tempfile.TemporaryDirectory() as directory:
            record_path = Path(directory) / "record.json"
            record_path.write_bytes(record_bytes)
            environment["RELEASE_RECORD"] = str(record_path)
            return subprocess.run(
                ["bash", ".github/publish_release.sh"],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, **environment},
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    def test_exact_policy_record_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DIGEST_ASSET
            path.write_bytes(record())
            prepare_inputs.validate_policy_digest(
                path, RELEASE, SOURCE_COMMIT, ANNOTATED_TAG_SHA
            )
            for invalid in (
                record(source_commit="d" * 40),
                record(annotated_tag_sha="e" * 40),
                record(extra="unexpected"),
                record(digest="f" * 64),
                record()[:-1] + b" ",
                b"x" * (prepare_inputs.MAX_DIGEST_JSON_BYTES + 1),
            ):
                path.write_bytes(invalid)
                with self.assertRaises(SystemExit):
                    prepare_inputs.validate_policy_digest(
                        path, RELEASE, SOURCE_COMMIT, ANNOTATED_TAG_SHA
                    )

    def test_exact_release_assets_and_wheel_metadata(self) -> None:
        metadata = release_metadata()
        wheel_asset, digest_asset = prepare_inputs.validate_policy_assets(
            metadata, RELEASE, WHEEL, WHEEL_SHA
        )
        self.assertEqual(wheel_asset["name"], WHEEL)
        self.assertEqual(digest_asset["name"], DIGEST_ASSET)
        metadata["assets"] = metadata["assets"] + [asset("extra", 3, 1, "1" * 64)]
        with self.assertRaises(SystemExit):
            prepare_inputs.validate_policy_assets(metadata, RELEASE, WHEEL, WHEEL_SHA)

    def test_exact_release_metadata_contract_and_rejects_drift(self) -> None:
        metadata = release_metadata()
        for html_url in (
            f"https://github.com/TeleCrypt-io/control-plane/releases/tag/{RELEASE}",
            f"https://github.com/TeleCrypt-io/control-plane/releases/{RELEASE}",
        ):
            prepare_inputs.validate_policy_release(
                {**metadata, "html_url": html_url}, RELEASE
            )
        for field, value in (
            ("name", "wrong-name"),
            ("id", 0),
            ("published_at", "2026-02-30T00:00:00Z"),
            ("published_at", "2026-08-23 00:00:00Z"),
            ("url", "https://api.github.com/other"),
            ("html_url", "https://github.com/TeleCrypt-io/control-plane/releases/tag/other"),
        ):
            invalid = dict(metadata)
            invalid[field] = value
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_policy_release(invalid, RELEASE)

    def test_resolve_latest_policy_uses_highest_published_stable_release_once(self) -> None:
        latest = "0.5.0"
        latest_metadata = release_metadata(latest)
        responses = {
            "releases?per_page=100&page=1": [
                {"tag_name": "0.4.0", "draft": False, "prerelease": False},
                {"tag_name": "0.9.0", "draft": True, "prerelease": False},
                {"tag_name": "0.8.0", "draft": False, "prerelease": True},
                {"tag_name": latest, "draft": False, "prerelease": False},
            ],
            f"releases/tags/{latest}": latest_metadata,
        }
        with mock.patch.object(
            prepare_inputs,
            "fetch_policy_api",
            side_effect=lambda endpoint: responses[endpoint],
        ) as fetch:
            self.assertEqual(
                prepare_inputs.resolve_latest_policy(),
                (latest, WHEEL_SHA),
            )
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            ["releases?per_page=100&page=1", f"releases/tags/{latest}"],
        )

    def test_resolve_latest_policy_requires_a_published_stable_release(self) -> None:
        with mock.patch.object(
            prepare_inputs,
            "fetch_policy_api",
            return_value=[
                {"tag_name": "0.4.0", "draft": True, "prerelease": False},
                {"tag_name": "0.3.0", "draft": False, "prerelease": True},
                {"tag_name": "invalid", "draft": False, "prerelease": False},
            ],
        ):
            with self.assertRaises(SystemExit):
                prepare_inputs.resolve_latest_policy()

    def test_fork_release_requires_immutable_source_only_release(self) -> None:
        metadata = fork_release_metadata()
        prepare_inputs.validate_fork_release(metadata, FORK_REPOSITORY, FORK_RELEASE)
        for field, value in (
            ("immutable", False),
            ("draft", True),
            ("prerelease", True),
            ("assets", [{}]),
            ("name", "wrong-name"),
            ("upload_url", "https://uploads.github.com/other"),
        ):
            invalid = dict(metadata)
            invalid[field] = value
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_fork_release(invalid, FORK_REPOSITORY, FORK_RELEASE)

    def test_fork_release_tag_is_verified_against_immutable_release(self) -> None:
        responses = {
            f"releases/tags/{FORK_RELEASE}": fork_release_metadata(),
            f"git/ref/tags/{FORK_RELEASE}": {
                "ref": f"refs/tags/{FORK_RELEASE}",
                "url": f"https://api.github.com/repos/{FORK_REPOSITORY}/git/refs/tags/{FORK_RELEASE}",
                "object": {
                    "type": "tag",
                    "sha": FORK_ANNOTATED_TAG_SHA,
                    "url": f"https://api.github.com/repos/{FORK_REPOSITORY}/git/tags/{FORK_ANNOTATED_TAG_SHA}",
                },
            },
            f"git/tags/{FORK_ANNOTATED_TAG_SHA}": {
                "sha": FORK_ANNOTATED_TAG_SHA,
                "tag": FORK_RELEASE,
                "url": f"https://api.github.com/repos/{FORK_REPOSITORY}/git/tags/{FORK_ANNOTATED_TAG_SHA}",
                "object": {
                    "type": "commit",
                    "sha": FORK_SOURCE_COMMIT,
                    "url": f"https://api.github.com/repos/{FORK_REPOSITORY}/git/commits/{FORK_SOURCE_COMMIT}",
                },
            },
        }
        with mock.patch.object(
            prepare_inputs,
            "fetch_github_api",
            side_effect=lambda repository, endpoint, label: responses[endpoint],
        ):
            self.assertEqual(
                prepare_inputs.fetch_fork_release(
                    FORK_REPOSITORY, FORK_RELEASE, FORK_SOURCE_COMMIT
                ),
                (
                    f"https://api.github.com/repos/{FORK_REPOSITORY}/tarball/{FORK_RELEASE}",
                    f"TeleCrypt-io-fork-synapse-{FORK_ANNOTATED_TAG_SHA[:7]}",
                ),
            )
        with mock.patch.object(
            prepare_inputs,
            "fetch_github_api",
            side_effect=lambda repository, endpoint, label: {
                **responses[endpoint],
                **({"immutable": False} if endpoint.startswith("releases/") else {}),
            },
        ):
            with self.assertRaises(SystemExit):
                prepare_inputs.fetch_fork_release(FORK_REPOSITORY, FORK_RELEASE, FORK_SOURCE_COMMIT)

    def test_asset_state_and_api_url_are_exact(self) -> None:
        for field, value in (
            ("state", "new"),
            ("label", None),
            ("label", "unexpected"),
            (
                "url",
                "https://api.github.com/repos/TeleCrypt-io/control-plane/releases/assets/9",
            ),
            (
                "url",
                "https://user:password@api.github.com/repos/TeleCrypt-io/control-plane/releases/assets/1",
            ),
        ):
            metadata = release_metadata()
            metadata["assets"] = [dict(item) for item in metadata["assets"]]
            metadata["assets"][0][field] = value
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_policy_assets(metadata, RELEASE, WHEEL, WHEEL_SHA)

    def test_download_urls_reject_userinfo_and_redirect_userinfo(self) -> None:
        for url in (
            "https://user@example.com/archive.tar.gz",
            "https://:password@github.com/archive.tar.gz",
            "https://@github.com/archive.tar.gz",
            "https://user:@github.com/archive.tar.gz",
        ):
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_download_url(url, "github.com")
            request = urllib.request.Request("https://github.com/start")
            with self.assertRaises(ValueError):
                prepare_inputs.SafeRedirectHandler().redirect_request(
                    request, None, 307, "temporary", {}, url
                )

    def test_provider_archive_rejects_unsafe_members_without_extracting(self) -> None:
        def archive(entries: list[tuple[str, str]]) -> bytes:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as output:
                for kind, name in entries:
                    member = tarfile.TarInfo(name)
                    if kind == "file":
                        payload = b""
                        member.size = len(payload)
                        output.addfile(member, io.BytesIO(payload))
                    elif kind == "directory":
                        member.type = tarfile.DIRTYPE
                        output.addfile(member)
                    elif kind == "symlink":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "../../outside"
                        output.addfile(member)
                    elif kind == "hardlink":
                        member.type = tarfile.LNKTYPE
                        member.linkname = "provider/setup.py"
                        output.addfile(member)
                    elif kind == "fifo":
                        member.type = tarfile.FIFOTYPE
                        output.addfile(member)
                    else:
                        raise AssertionError(kind)
            return stream.getvalue()

        invalid_entries = (
            [("file", "/absolute/setup.py")],
            [("file", "provider/../setup.py")],
            [("file", "provider\\escape/setup.py")],
            [("file", "provider\u2216escape/setup.py")],
            [("file", "provider/setup.py"), ("file", "provider/setup.py")],
            [("file", "provider/setup.py"), ("file", "provider/setup.py/child")],
            [("directory", "provider/setup.py"), ("file", "provider/setup.py")],
            [("symlink", "provider/link")],
            [("hardlink", "provider/link")],
            [("fifo", "provider/fifo")],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.tar.gz"
            path.write_bytes(
                archive(
                    [
                        ("directory", "TeleCrypt-io-fork-synapse-s3-storage-provider-d8bb991"),
                        ("file", "TeleCrypt-io-fork-synapse-s3-storage-provider-d8bb991/setup.py"),
                    ]
                )
            )
            prepare_inputs.validate_provider_build_contract(
                path, "TeleCrypt-io-fork-synapse-s3-storage-provider-d8bb991"
            )
        for entries in invalid_entries:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "provider.tar.gz"
                path.write_bytes(archive(entries))
                with self.assertRaises(SystemExit, msg=entries):
                    prepare_inputs.validate_provider_build_contract(path, "provider")

        class FakeMember:
            def __init__(self, name: str, size: int = 0) -> None:
                self.name = name
                self.size = size

            def isfile(self) -> bool:
                return True

            def isdir(self) -> bool:
                return False

        class FakeArchive:
            def __init__(self, members: list[FakeMember]) -> None:
                self.members = members

            def __enter__(self) -> "FakeArchive":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def __iter__(self):
                return iter(self.members)

        for members in (
            [FakeMember(f"provider/file-{index}") for index in range(prepare_inputs.MAX_ARCHIVE_MEMBERS + 1)],
            [FakeMember("provider/setup.py", prepare_inputs.MAX_ARCHIVE_UNCOMPRESSED_BYTES + 1)],
        ):
            with mock.patch.object(prepare_inputs.tarfile, "open", return_value=FakeArchive(members)):
                with self.assertRaises(SystemExit):
                    prepare_inputs.validate_provider_build_contract(
                        Path("unused.tar.gz"), "provider"
                    )

    def test_publish_release_rejects_noncanonical_record_before_network(self) -> None:
        publish_record = record(tag="1.159-tc3")
        for invalid in (
            record(tag="1.159-tc3", extra="unexpected"),
            publish_record[:-1] + b" ",
        ):
            result = self.run_publish_release(invalid)
            self.assertNotEqual(result.returncode, 0)

    def test_publish_release_rejects_noncanonical_tags_before_network(self) -> None:
        for tag in (
            "01.159-tc3",
            "1.0159-tc3",
            "1.159-tc0",
            "1.159-tc03",
            "1.159",
            "v1.159-tc3",
        ):
            result = self.run_publish_release(
                record(tag=tag), EXPECTED_TAG=tag
            )
            self.assertNotEqual(result.returncode, 0, tag)

    def test_release_record_rejects_noncanonical_tags(self) -> None:
        for tag in (
            "01.159-tc3",
            "1.0159-tc3",
            "1.159-tc0",
            "1.159-tc03",
            "1.159",
            "v1.159-tc3",
        ):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "record.json"
                environment = {
                    **os.environ,
                    "RECORD_PATH": str(output),
                    "RECORD_IMAGE": "ghcr.io/telecrypt-io/telecrypt-synapse",
                    "RECORD_TAG": tag,
                    "RECORD_DIGEST": "sha256:" + "c" * 64,
                    "RECORD_SOURCE_SHA": SOURCE_COMMIT,
                    "RECORD_ANNOTATED_TAG_SHA": ANNOTATED_TAG_SHA,
                }
                result = subprocess.run(
                    ["python3", ".github/release_record.py"],
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0, tag)
                self.assertFalse(output.exists(), tag)

    def test_publish_release_requires_reviewed_github_api_version(self) -> None:
        result = self.run_publish_release(
            record(tag="1.159-tc3"), GH_API_VERSION="2025-01-01"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_publish_release_refuses_a_preexisting_final_release(self) -> None:
        payload = record(tag="1.159-tc3", image="ghcr.io/telecrypt-io/telecrypt-synapse")
        digest = "sha256:" + __import__("hashlib").sha256(payload).hexdigest()
        release = {
            "id": 9,
            "tag_name": "1.159-tc3",
            "name": "1.159-tc3",
            "body": f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "assets": [{
                "id": 10,
                "name": "telecrypt-synapse-1.159-tc3.digest.json",
                "label": "",
                "state": "uploaded",
                "size": len(payload),
                "digest": digest,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            fake_gh = directory_path / "gh"
            log = directory_path / "gh.log"
            response = directory_path / "release.json"
            response.write_text(json.dumps(release), encoding="utf-8")
            fake_gh.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_GH_LOG\"\n"
                "if [ \"$1\" = api ]; then\n"
                "  printf 'HTTP/1.1 200 OK\\n\\n'\n"
                "  case \"$*\" in\n"
                "    *'releases?per_page=100&page=1'*) printf '['; cat \"$FAKE_GH_RESPONSE\"; printf ']\\n';;\n"
                "    *'releases/9'*) cat \"$FAKE_GH_RESPONSE\";;\n"
                "    *) exit 98;;\n"
                "  esac\n"
                "  exit 0\n"
                "fi\n"
                "exit 99\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            environment = {
                "PATH": f"{directory}:{os.environ['PATH']}",
                "FAKE_GH_LOG": str(log),
                "FAKE_GH_RESPONSE": str(response),
            }
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                **environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), [
                "api --include --hostname github.com --header Accept: application/vnd.github+json "
                "--header X-GitHub-Api-Version: 2026-03-10 "
                "repos/TeleCrypt-io/synapse-server/releases?per_page=100&page=1",
                "api --include --hostname github.com --header Accept: application/vnd.github+json "
                "--header X-GitHub-Api-Version: 2026-03-10 "
                "repos/TeleCrypt-io/synapse-server/releases/9",
            ])

    def test_publish_release_reuses_numeric_draft_and_uses_numeric_mutations(self) -> None:
        payload = synapse_record()
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            fake_gh = directory_path / "gh"
            log = directory_path / "gh.log"
            state = directory_path / "state.json"
            state.write_text(json.dumps({"exists": True, "asset": False, "published": False}), encoding="utf-8")
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import hashlib, json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "with Path(os.environ['FAKE_GH_LOG']).open('a', encoding='utf-8') as output:\n"
                "    output.write(json.dumps(args) + '\\n')\n"
                "endpoint = next((value for value in args if value.startswith(('repos/', 'https://'))), '')\n"
                "state_path = Path(os.environ['FAKE_GH_STATE'])\n"
                "state = json.loads(state_path.read_text(encoding='utf-8'))\n"
                "record = Path(os.environ['RELEASE_RECORD']).read_bytes()\n"
                "asset = {'id': 321, 'name': os.environ['RELEASE_ASSET_NAME'], 'label': '',\n"
                "         'state': 'uploaded', 'size': len(record),\n"
                "         'digest': 'sha256:' + hashlib.sha256(record).hexdigest()}\n"
                "release = {'id': 123, 'tag_name': os.environ['EXPECTED_TAG'],\n"
                "           'name': os.environ['EXPECTED_TAG'],\n"
                "           'body': 'Exact Synapse release for source commit ' + os.environ['EXPECTED_SHA'] + '.',\n"
                "           'draft': not state['published'], 'prerelease': False,\n"
                "           'immutable': state['published'], 'assets': [asset] if state['asset'] else []}\n"
                "if endpoint.endswith('releases?per_page=100&page=1'):\n"
                "    delayed = os.environ.get('FAKE_CREATE_LIST_DELAY') and state.get('created')\n"
                "    response, status = ([release] if state['exists'] and not delayed else []), 200\n"
                "elif endpoint == 'repos/TeleCrypt-io/synapse-server/releases' and '--method' in args:\n"
                "    state['exists'] = True\n"
                "    state['created'] = True\n"
                "    state_path.write_text(json.dumps(state), encoding='utf-8')\n"
                "    response, status = release, 201\n"
                "elif endpoint == 'repos/TeleCrypt-io/synapse-server/releases/123' and '--method' in args:\n"
                "    if os.environ.get('FAKE_EDIT_COMMITTED_FAILURE'):\n"
                "        state['published'] = True\n"
                "        state_path.write_text(json.dumps(state), encoding='utf-8')\n"
                "        sys.stderr.write('fixture publish response lost\\n')\n"
                "        raise SystemExit(19)\n"
                "    if os.environ.get('FAKE_EDIT_FAILURE'):\n"
                "        sys.stderr.write('fixture publish rejected\\n')\n"
                "        raise SystemExit(18)\n"
                "    state['published'] = True\n"
                "    state_path.write_text(json.dumps(state), encoding='utf-8')\n"
                "    release['draft'] = False\n"
                "    release['immutable'] = True\n"
                "    response, status = release, 200\n"
                "elif endpoint == 'repos/TeleCrypt-io/synapse-server/releases/123':\n"
                "    response, status = release, 200\n"
                "elif endpoint == 'https://uploads.github.com/repos/TeleCrypt-io/synapse-server/releases/123/assets?name=' + os.environ['RELEASE_ASSET_NAME']:\n"
                "    if os.environ.get('FAKE_UPLOAD_FAILURE'):\n"
                "        sys.stderr.write('fixture upload rejected\\n')\n"
                "        raise SystemExit(17)\n"
                "    if not os.environ.get('FAKE_UPLOAD_NO_ASSET'):\n"
                "        state['asset'] = True\n"
                "        state_path.write_text(json.dumps(state), encoding='utf-8')\n"
                "    response, status = asset, 201\n"
                "elif endpoint == 'repos/TeleCrypt-io/synapse-server/releases/assets/321':\n"
                "    sys.stdout.buffer.write(record)\n"
                "    raise SystemExit(0)\n"
                "else:\n"
                "    raise SystemExit('unexpected endpoint: ' + endpoint)\n"
                "if '--include' in args:\n"
                "    sys.stdout.write('HTTP/1.1 ' + str(status) + ' OK\\n\\n')\n"
                "json.dump(response, sys.stdout, separators=(',', ':'))\n"
                "sys.stdout.write('\\n')\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
            )
            calls_text = log.read_text(encoding="utf-8") if log.exists() else "<no calls>"
            self.assertEqual(result.returncode, 0, result.stderr + "\n" + calls_text)
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            endpoints = [value for call in calls for value in call if value.startswith(("repos/", "https://"))]
            self.assertTrue(any("releases?per_page=100&page=1" in value for value in endpoints))
            self.assertTrue(any(value.endswith("releases/123") for value in endpoints))
            self.assertTrue(any(value == "https://uploads.github.com/repos/TeleCrypt-io/synapse-server/releases/123/assets?name=telecrypt-synapse-1.159-tc3.digest.json" for value in endpoints))
            self.assertFalse(any(value == "uploads.github.com" for call in calls for value in call))
            self.assertTrue(any(value.endswith("releases/assets/321") for value in endpoints))
            self.assertFalse(any("releases/tags/" in value for value in endpoints))
            self.assertFalse(any(value == "repos/TeleCrypt-io/synapse-server/releases" for value in endpoints))

            log.write_text("", encoding="utf-8")
            state.write_text(json.dumps({"exists": False, "asset": False, "published": False}), encoding="utf-8")
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
            )
            calls_text = log.read_text(encoding="utf-8") if log.exists() else "<no calls>"
            self.assertEqual(result.returncode, 0, result.stderr + "\n" + calls_text)
            calls = [json.loads(line) for line in calls_text.splitlines()]
            endpoints = [value for call in calls for value in call if value.startswith(("repos/", "https://"))]
            self.assertTrue(any(value == "repos/TeleCrypt-io/synapse-server/releases" for value in endpoints))
            self.assertTrue(any(value.endswith("releases/123") for value in endpoints))

            log.write_text("", encoding="utf-8")
            state.write_text(json.dumps({"exists": False, "asset": False, "published": False}), encoding="utf-8")
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
                FAKE_CREATE_LIST_DELAY="1",
            )
            calls_text = log.read_text(encoding="utf-8") if log.exists() else "<no calls>"
            self.assertEqual(result.returncode, 0, result.stderr + "\n" + calls_text)
            calls = [json.loads(line) for line in calls_text.splitlines()]
            list_calls = [
                call for call in calls
                if any("releases?per_page=100&page=1" in value for value in call)
            ]
            self.assertEqual(len(list_calls), 1)
            self.assertTrue(any("releases/123" in value for call in calls for value in call))

            log.write_text("", encoding="utf-8")
            state.write_text(
                json.dumps({"exists": True, "asset": False, "published": False}),
                encoding="utf-8",
            )
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
                FAKE_UPLOAD_FAILURE="1",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("fixture upload rejected", result.stderr)
            self.assertIn("release asset upload failed (status 17)", result.stderr)

            log.write_text("", encoding="utf-8")
            state.write_text(
                json.dumps({"exists": True, "asset": False, "published": False}),
                encoding="utf-8",
            )
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
                FAKE_UPLOAD_NO_ASSET="1",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("release draft does not contain the exact uploaded asset", result.stderr)

            log.write_text("", encoding="utf-8")
            state.write_text(
                json.dumps({"exists": True, "asset": True, "published": False}),
                encoding="utf-8",
            )
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
                FAKE_EDIT_FAILURE="1",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("fixture publish rejected", result.stderr)
            self.assertIn("release publication failed after PATCH (status 18)", result.stderr)

            log.write_text("", encoding="utf-8")
            state.write_text(
                json.dumps({"exists": True, "asset": True, "published": False}),
                encoding="utf-8",
            )
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
                FAKE_GH_STATE=str(state),
                FAKE_EDIT_COMMITTED_FAILURE="1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("fixture publish response lost", result.stderr)
            self.assertIn("exact immutable readback resolved the outcome", result.stderr)

    def test_publish_release_fails_closed_on_duplicate_exact_tag_matches(self) -> None:
        payload = synapse_record()
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            fake_gh = directory_path / "gh"
            log = directory_path / "gh.log"
            fake_gh.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_GH_LOG\"\n"
                "printf 'HTTP/1.1 200 OK\\n\\n'\n"
                "printf '[{\"id\":123,\"tag_name\":\"1.159-tc3\"},{\"id\":124,\"tag_name\":\"1.159-tc3\"}]\\n'\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            result = self.run_publish_release(
                payload,
                RELEASE_ASSET_NAME="telecrypt-synapse-1.159-tc3.digest.json",
                PATH=f"{directory}:{os.environ['PATH']}",
                FAKE_GH_LOG=str(log),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("releases/123", log.read_text(encoding="utf-8"))
            self.assertNotIn("releases/124", log.read_text(encoding="utf-8"))


    def test_synapse_release_contract_checks_identity_and_asset_digest(self) -> None:
        digest = "sha256:" + "d" * 64
        document = {
            "id": 7,
            "tag_name": "1.159-tc3",
            "name": "1.159-tc3",
            "body": f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "created_at": "2026-08-22T00:00:00Z",
            "published_at": "2026-08-23T00:00:00Z",
            "url": "https://api.github.com/repos/TeleCrypt-io/synapse-server/releases/7",
            "assets_url": "https://api.github.com/repos/TeleCrypt-io/synapse-server/releases/7/assets",
            "upload_url": "https://uploads.github.com/repos/TeleCrypt-io/synapse-server/releases/7/assets{?name,label}",
            "html_url": "https://github.com/TeleCrypt-io/synapse-server/releases/tag/1.159-tc3",
            "tarball_url": "https://api.github.com/repos/TeleCrypt-io/synapse-server/tarball/1.159-tc3",
            "zipball_url": "https://api.github.com/repos/TeleCrypt-io/synapse-server/zipball/1.159-tc3",
            "assets": [{
                "name": "telecrypt-synapse-1.159-tc3.digest.json",
                "id": 8,
                "label": "",
                "state": "uploaded",
                "size": 10,
                "url": "https://api.github.com/repos/TeleCrypt-io/synapse-server/releases/assets/8",
                "browser_download_url": "https://github.com/TeleCrypt-io/synapse-server/releases/download/1.159-tc3/telecrypt-synapse-1.159-tc3.digest.json",
                "digest": digest,
                "created_at": "2026-08-22T00:00:01Z",
                "updated_at": "2026-08-22T00:00:02Z",
            }],
        }
        validate_release.validate_release(
            document,
            tag="1.159-tc3",
            asset_name="telecrypt-synapse-1.159-tc3.digest.json",
            body=f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
            record_digest=digest,
            record_size=10,
        )
        for changes in (
            {"digest": "sha256:" + "e" * 64},
            {"label": None},
            {"label": "unexpected"},
        ):
            invalid = dict(document)
            invalid["assets"] = [dict(document["assets"][0], **changes)]
            with self.assertRaises(SystemExit):
                validate_release.validate_release(
                    invalid,
                    tag="1.159-tc3",
                    asset_name="telecrypt-synapse-1.159-tc3.digest.json",
                    body=f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
                    record_digest=digest,
                    record_size=10,
                )

    def test_annotated_tag_is_peeled_to_commit(self) -> None:
        responses = {
            f"git/ref/tags/{RELEASE}": {
                "ref": f"refs/tags/{RELEASE}",
                "url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/git/refs/tags/{RELEASE}",
                "object": {
                    "type": "tag",
                    "sha": ANNOTATED_TAG_SHA,
                    "url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/git/tags/{ANNOTATED_TAG_SHA}",
                },
            },
            f"git/tags/{ANNOTATED_TAG_SHA}": {
                "sha": ANNOTATED_TAG_SHA,
                "tag": RELEASE,
                "url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/git/tags/{ANNOTATED_TAG_SHA}",
                "object": {
                    "type": "commit",
                    "sha": SOURCE_COMMIT,
                    "url": f"https://api.github.com/repos/TeleCrypt-io/control-plane/git/commits/{SOURCE_COMMIT}",
                },
            },
        }
        original = prepare_inputs.fetch_policy_api
        prepare_inputs.fetch_policy_api = lambda endpoint: responses[endpoint]
        try:
            self.assertEqual(
                prepare_inputs.fetch_policy_annotated_tag(RELEASE),
                (ANNOTATED_TAG_SHA, SOURCE_COMMIT),
            )
            responses[f"git/ref/tags/{RELEASE}"]["object"]["type"] = "commit"
            with self.assertRaises(SystemExit):
                prepare_inputs.fetch_policy_annotated_tag(RELEASE)
        finally:
            prepare_inputs.fetch_policy_api = original

    def test_build_provenance_binds_the_inspected_base_digest(self) -> None:
        digest = "sha256:" + "1" * 64
        metadata = {
            "buildx.build.provenance": {
                "materials": [
                    {
                        "uri": f"pkg:docker/ghcr.io/element-hq/synapse@{digest}",
                        "digest": {"sha256": "1" * 64},
                    }
                ]
            }
        }
        verify_base_provenance.validate_metadata(
            metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
        )
        invalid_metadata = dict(metadata)
        invalid_metadata["buildx.build.provenance"] = {"materials": []}
        with self.assertRaises(SystemExit):
            verify_base_provenance.validate_metadata(
                invalid_metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
            )

    def test_build_provenance_accepts_exact_tagged_amd64_material(self) -> None:
        digest = "sha256:" + "3" * 64
        metadata = {
            "buildx.build.provenance": {
                "materials": [
                    {
                        "uri": (
                            "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0"
                            f"?digest={digest}&platform=linux%2Famd64"
                        ),
                        "digest": {"sha256": "3" * 64},
                    }
                ]
            }
        }
        verify_base_provenance.validate_metadata(
            metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
        )
        metadata["buildx.build.provenance"]["materials"][0]["digest"]["sha256"] = "4" * 64
        with self.assertRaises(SystemExit):
            verify_base_provenance.validate_metadata(
                metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
            )
        metadata["buildx.build.provenance"]["materials"][0]["digest"]["sha256"] = "3" * 64
        metadata["buildx.build.provenance"]["materials"][0]["uri"] = (
            "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0"
            f"?digest={'sha256:' + '4' * 64}&platform=linux%2Famd64"
        )
        with self.assertRaises(SystemExit):
            verify_base_provenance.validate_metadata(
                metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
            )

    def test_build_provenance_accepts_v1_tagged_amd64_dependency(self) -> None:
        digest = "sha256:" + "2" * 64
        metadata = {
            "buildx.build.provenance": {
                "buildDefinition": {
                    "resolvedDependencies": [
                        {
                            "uri": "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0?platform=linux%2Famd64",
                            "digest": {"sha256": "2" * 64},
                        }
                    ]
                }
            },
            "buildx.build.warnings": [],
        }
        verify_base_provenance.validate_metadata(
            metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
        )
        for invalid in (
            {
                **metadata,
                "buildx.build.provenance": {
                    "buildDefinition": {
                        "resolvedDependencies": [
                            {
                                "uri": "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0?platform=linux%2Farm64",
                                "digest": {"sha256": "2" * 64},
                            }
                        ]
                    }
                },
            },
            {**metadata, "buildx.build.warnings": [{"message": "warning"}]},
        ):
            with self.assertRaises(SystemExit):
                verify_base_provenance.validate_metadata(
                    invalid, "ghcr.io/element-hq/synapse:v1.159.0", digest
                )


if __name__ == "__main__":
    unittest.main()
