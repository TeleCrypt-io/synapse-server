#!/usr/bin/env python3
"""Download and verify every non-base input before the offline image build."""

from __future__ import annotations

import argparse
import datetime
import hashlib
from http.client import IncompleteRead
import json
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from urllib.error import HTTPError
import urllib.request
from urllib.parse import urljoin, urlsplit
from pathlib import Path

from validate_dependencies import MAX_FILE_BYTES, load_lock, validate_wheelhouse


HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
FORK_RELEASE_RE = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-telecrypt\.[1-9][0-9]*\Z"
)
DOWNLOAD_TIMEOUT_SECONDS = 60
PIP_SUBPROCESS_TIMEOUT_SECONDS = 300
GITHUB_API_VERSION = "2026-03-10"
GITHUB_API_ROOT = "https://api.github.com"
GITHUB_API_ACCEPT = "application/vnd.github+json"
BINARY_ACCEPT = "application/octet-stream"
POLICY_REPOSITORY = "TeleCrypt-io/control-plane"
SYNAPSE_FORK_REPOSITORY = "TeleCrypt-io/fork-synapse"
S3_PROVIDER_FORK_REPOSITORY = "TeleCrypt-io/fork-synapse-s3-storage-provider"
POLICY_IMAGE = "ghcr.io/telecrypt-io/controlplane"
MAX_DIGEST_JSON_BYTES = 64 * 1024
MAX_TOTAL_INPUT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_NAME_BYTES = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
BACKSLASH_CONFUSABLES = frozenset("\\\u2216\u29f5\ufe68\uff3c")
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "api.github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


def fail(message: str) -> None:
    raise SystemExit(f"prepare inputs: {message}")


def synapse_fork_archive_name(release: str) -> str:
    return f"synapse-{release}.tar.gz"


def s3_provider_fork_archive_name(release: str) -> str:
    return f"synapse-s3-storage-provider-{release}.tar.gz"


def run_pip(command: list[str]) -> None:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        fail(f"could not start pip download: {exc}")

    def report_output(stdout: bytes, stderr: bytes) -> None:
        for name, output in (("stdout", stdout), ("stderr", stderr)):
            if output:
                diagnostics = output.decode("utf-8", errors="replace")
                print(f"pip download {name}:", file=sys.stderr)
                print(diagnostics, end="" if diagnostics.endswith("\n") else "\n", file=sys.stderr)

    try:
        stdout, stderr = process.communicate(timeout=PIP_SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        report_output(stdout, stderr)
        fail(f"pip download exceeded {PIP_SUBPROCESS_TIMEOUT_SECONDS} seconds")
    report_output(stdout, stderr)
    return_code = process.returncode
    if return_code != 0:
        fail(f"pip download failed with exit code {return_code}")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        target = urlsplit(urljoin(request.full_url, new_url))
        if (
            target.scheme != "https"
            or target.hostname not in ALLOWED_DOWNLOAD_HOSTS
            or target.username is not None
            or target.password is not None
            or target.port is not None
            or target.fragment
        ):
            raise ValueError(f"redirect leaves the approved GitHub hosts: {target.geturl()}")
        return super().redirect_request(request, file, code, message, headers, new_url)


URL_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def validate_download_url(url: str, expected_host: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
    ):
        fail(f"unexpected download URL: {url}")
    if parsed.port is not None or parsed.fragment:
        fail(f"unexpected download URL components: {url}")


def download(
    url: str,
    destination: Path,
    expected: str,
    *,
    expected_host: str = "github.com",
    accept: str = BINARY_ACCEPT,
    expected_size: int | None = None,
    max_bytes: int = MAX_FILE_BYTES,
) -> None:
    temporary: Path | None = None
    try:
        validate_download_url(url, expected_host)
        if accept not in {BINARY_ACCEPT, GITHUB_API_ACCEPT}:
            raise ValueError("download media type is not approved")
        if expected_size is not None and (expected_size <= 0 or expected_size > max_bytes):
            raise ValueError(f"advertised size is outside the {max_bytes} byte file limit")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            total_bytes = 0
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": accept,
                    "User-Agent": "telecrypt-synapse-build",
                },
            )
            with URL_OPENER.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                while chunk := response.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise ValueError(f"download exceeds the {max_bytes} byte file limit")
                    digest.update(chunk)
                    output.write(chunk)
        if expected_size is not None and total_bytes != expected_size:
            raise ValueError(f"{destination.name} has size {total_bytes}, expected {expected_size}")
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(f"{destination.name} has digest {actual}, expected {expected}")
        temporary.replace(destination)
    except Exception as exc:
        retained = f"; retained temporary input: {temporary}" if temporary is not None else ""
        fail(f"could not download or verify {destination.name}: {exc}{retained}")


def read_response(response) -> bytes:
    return response.read()


def fetch_github_api(repository: str, endpoint: str, label: str) -> dict:
    url = f"{GITHUB_API_ROOT}/repos/{repository}/{endpoint}"
    validate_download_url(url, "api.github.com")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": GITHUB_API_ACCEPT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "telecrypt-synapse-build",
        },
    )
    try:
        with URL_OPENER.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            if response.geturl() != url:
                fail(f"{label} API redirected unexpectedly: {response.geturl()}")
            payload = read_response(response)
    except SystemExit:
        raise
    except HTTPError as exc:
        body_bytes = b""
        read_error: Exception | None = None
        close_error: Exception | None = None
        try:
            body_bytes = exc.read()
        except IncompleteRead as error:
            body_bytes = error.partial
            read_error = error
        except Exception as error:
            read_error = error
        finally:
            try:
                exc.close()
            except Exception as error:
                close_error = error
        body = body_bytes.decode("utf-8", errors="replace")
        details = (
            f"could not fetch {label} API metadata: {type(exc).__name__}: {exc}"
            f"\nresponse body:\n{body}"
        )
        if read_error is not None:
            details += f"\nresponse read failed: {read_error}"
        if close_error is not None:
            details += f"\nresponse close failed: {close_error}"
        fail(details)
    except Exception as exc:
        fail(f"could not fetch {label} API metadata: {type(exc).__name__}: {exc}")
    try:
        metadata = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        body = payload.decode("utf-8", errors="replace")
        fail(
            f"{label} API metadata is not valid JSON: {type(exc).__name__}: {exc}"
            f"\nresponse body:\n{body}"
        )
    if not isinstance(metadata, dict):
        fail(f"{label} API metadata is not an object")
    return metadata


def fetch_policy_api(endpoint: str) -> dict:
    return fetch_github_api(POLICY_REPOSITORY, endpoint, "policy")


def fetch_fork_api(repository: str, endpoint: str) -> dict:
    return fetch_github_api(repository, endpoint, "fork")


def fetch_fork_annotated_tag(repository: str, release: str, expected_commit: str) -> str:
    ref = fetch_fork_api(repository, f"git/ref/tags/{release}")
    api_root = f"{GITHUB_API_ROOT}/repos/{repository}"
    ref_object = ref.get("object")
    if (
        ref.get("ref") != f"refs/tags/{release}"
        or ref.get("url") != f"{api_root}/git/refs/tags/{release}"
        or not isinstance(ref_object, dict)
        or ref_object.get("type") != "tag"
        or ref_object.get("url") != f"{api_root}/git/tags/{ref_object.get('sha')}"
    ):
        fail(f"fork release tag is not an annotated tag ref: {repository} {release}")
    annotated_tag_sha = ref_object.get("sha")
    if not isinstance(annotated_tag_sha, str) or not GIT_SHA_RE.fullmatch(annotated_tag_sha):
        fail("fork annotated tag ref has no exact tag-object SHA")
    tag_object = fetch_fork_api(repository, f"git/tags/{annotated_tag_sha}")
    if (
        tag_object.get("sha") != annotated_tag_sha
        or tag_object.get("tag") != release
        or tag_object.get("url") != f"{api_root}/git/tags/{annotated_tag_sha}"
    ):
        fail("fork annotated tag object has an unexpected tag name")
    target = tag_object.get("object")
    source_commit = target.get("sha") if isinstance(target, dict) else None
    if (
        not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("url") != f"{api_root}/git/commits/{source_commit}"
        or not isinstance(source_commit, str)
        or not GIT_SHA_RE.fullmatch(source_commit)
        or source_commit != expected_commit
    ):
        fail("fork release tag does not peel to the locked commit")
    return annotated_tag_sha


def fetch_policy_release(release: str) -> dict:
    metadata = fetch_policy_api(f"releases/tags/{release}")
    validate_policy_release(metadata, release)
    return metadata


def validate_policy_release(metadata: dict, release: str) -> None:
    expected_api_root = f"{GITHUB_API_ROOT}/repos/{POLICY_REPOSITORY}/releases"
    expected_html_urls = {
        f"https://github.com/{POLICY_REPOSITORY}/releases/{release}",
        f"https://github.com/{POLICY_REPOSITORY}/releases/tag/{release}",
    }
    if (
        metadata.get("tag_name") != release
        or metadata.get("name") != release
        or metadata.get("draft") is not False
        or metadata.get("prerelease") is not False
        or metadata.get("immutable") is not True
        or not isinstance(metadata.get("id"), int)
        or isinstance(metadata.get("id"), bool)
        or metadata["id"] <= 0
        or metadata.get("url")
        != f"{expected_api_root}/{metadata.get('id')}"
        or metadata.get("html_url") not in expected_html_urls
        or metadata.get("body") != f"Exact Controlplane release {release}."
        or metadata.get("assets_url")
        != f"{expected_api_root}/{metadata.get('id')}/assets"
        or metadata.get("upload_url")
        != f"https://uploads.github.com/repos/{POLICY_REPOSITORY}/releases/{metadata.get('id')}/assets{{?name,label}}"
        or metadata.get("tarball_url")
        != f"https://api.github.com/repos/{POLICY_REPOSITORY}/tarball/{release}"
        or metadata.get("zipball_url")
        != f"https://api.github.com/repos/{POLICY_REPOSITORY}/zipball/{release}"
    ):
        fail("policy release metadata is not the exact immutable release contract")
    def parse_timestamp(value: object, label: str) -> datetime.datetime:
        if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
            fail(f"policy {label} has no valid UTC timestamp")
        try:
            parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            fail(f"policy {label} has no valid UTC timestamp")
        if parsed.tzinfo != datetime.timezone.utc:
            fail(f"policy {label} is not UTC")
        return parsed

    created = parse_timestamp(metadata.get("created_at"), "release.created_at")
    published = parse_timestamp(metadata.get("published_at"), "release.published_at")
    if created > published:
        fail("policy release.created_at is after release.published_at")
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        fail("policy release assets are not a list")
    for asset in assets:
        if not isinstance(asset, dict):
            fail("policy release contains a malformed asset entry")
        asset_created = parse_timestamp(asset.get("created_at"), "asset.created_at")
        asset_updated = parse_timestamp(asset.get("updated_at"), "asset.updated_at")
        if not created <= asset_created <= asset_updated <= published:
            fail("policy release and asset timestamps are out of order")


def fetch_policy_annotated_tag(release: str) -> tuple[str, str]:
    ref = fetch_policy_api(f"git/ref/tags/{release}")
    api_root = f"{GITHUB_API_ROOT}/repos/{POLICY_REPOSITORY}"
    ref_object = ref.get("object")
    if (
        ref.get("ref") != f"refs/tags/{release}"
        or ref.get("url") != f"{api_root}/git/refs/tags/{release}"
        or not isinstance(ref_object, dict)
        or ref_object.get("type") != "tag"
        or ref_object.get("url") != f"{api_root}/git/tags/{ref_object.get('sha')}"
    ):
        fail("policy release tag is not an annotated tag ref")
    annotated_tag_sha = ref_object.get("sha")
    if not isinstance(annotated_tag_sha, str) or not GIT_SHA_RE.fullmatch(annotated_tag_sha):
        fail("policy annotated tag ref has no exact tag-object SHA")

    tag_object = fetch_policy_api(f"git/tags/{annotated_tag_sha}")
    if (
        tag_object.get("sha") != annotated_tag_sha
        or tag_object.get("tag") != release
        or tag_object.get("url") != f"{api_root}/git/tags/{annotated_tag_sha}"
    ):
        fail("policy annotated tag object has an unexpected tag name")
    target = tag_object.get("object")
    if not isinstance(target, dict) or target.get("type") != "commit":
        fail("policy annotated tag does not peel directly to a commit")
    if target.get("url") != f"{api_root}/git/commits/{target.get('sha')}":
        fail("policy annotated tag commit URL is not exact")
    source_commit = target.get("sha")
    if not isinstance(source_commit, str) or not GIT_SHA_RE.fullmatch(source_commit):
        fail("policy annotated tag has no exact peeled source commit")
    return annotated_tag_sha, source_commit


def validate_fork_release(metadata: dict, repository: str, release: str) -> None:
    expected_api_root = f"{GITHUB_API_ROOT}/repos/{repository}/releases"
    expected_html_urls = {
        f"https://github.com/{repository}/releases/{release}",
        f"https://github.com/{repository}/releases/tag/{release}",
    }
    if (
        metadata.get("tag_name") != release
        or metadata.get("name") != release
        or metadata.get("draft") is not False
        or metadata.get("prerelease") is not False
        or metadata.get("immutable") is not True
        or not isinstance(metadata.get("id"), int)
        or isinstance(metadata.get("id"), bool)
        or metadata["id"] <= 0
        or metadata.get("url") != f"{expected_api_root}/{metadata.get('id')}"
        or metadata.get("html_url") not in expected_html_urls
        or metadata.get("assets_url")
        != f"{expected_api_root}/{metadata.get('id')}/assets"
        or metadata.get("upload_url")
        != f"https://uploads.github.com/repos/{repository}/releases/{metadata.get('id')}/assets{{?name,label}}"
        or metadata.get("tarball_url")
        != f"{GITHUB_API_ROOT}/repos/{repository}/tarball/{release}"
        or metadata.get("zipball_url")
        != f"{GITHUB_API_ROOT}/repos/{repository}/zipball/{release}"
        or metadata.get("assets") != []
    ):
        fail(f"{repository} fork release is not the exact immutable source-only contract")


def fetch_fork_release(repository: str, release: str, expected_commit: str) -> tuple[str, str]:
    metadata = fetch_github_api(repository, f"releases/tags/{release}", f"{repository} fork release")
    validate_fork_release(metadata, repository, release)
    annotated_tag_sha = fetch_fork_annotated_tag(repository, release, expected_commit)
    archive_root = f"{repository.replace('/', '-')}-{annotated_tag_sha[:7]}"
    return metadata["tarball_url"], archive_root


def validate_policy_assets(
    metadata: dict, release: str, wheel: str, expected_wheel_sha256: str
) -> tuple[dict, dict]:
    assets = metadata.get("assets")
    if not isinstance(assets, list) or len(assets) != 2:
        fail("policy release must contain exactly the wheel and digest JSON assets")
    if any(not isinstance(asset, dict) for asset in assets):
        fail("policy release contains a malformed asset entry")
    names = [asset.get("name") for asset in assets]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != 2:
        fail("policy release assets must have two unique names")
    expected_digest_name = f"controlplane-{release}.digest.json"
    if set(names) != {wheel, expected_digest_name}:
        fail("policy release assets differ from the exact wheel and digest JSON pair")

    found = {asset["name"]: asset for asset in assets}
    wheel_asset = found[wheel]
    digest_asset = found[expected_digest_name]
    expected_wheel_url = (
        f"https://github.com/{POLICY_REPOSITORY}/releases/download/{release}/{wheel}"
    )
    expected_digest_url = (
        f"https://github.com/{POLICY_REPOSITORY}/releases/download/{release}/"
        f"{expected_digest_name}"
    )
    for asset, expected_url, label in (
        (wheel_asset, expected_wheel_url, "wheel"),
        (digest_asset, expected_digest_url, "digest JSON"),
    ):
        if asset.get("state") != "uploaded":
            fail(f"policy {label} asset is not uploaded")
        if "label" not in asset or asset.get("label") != "":
            fail(f"policy {label} asset has an unexpected label")
        if asset.get("browser_download_url") != expected_url:
            fail(f"policy {label} asset URL is not the exact release URL")
        if (
            not isinstance(asset.get("id"), int)
            or isinstance(asset["id"], bool)
            or asset["id"] <= 0
        ):
            fail(f"policy {label} asset has no valid immutable API ID")
        expected_api_url = (
            f"{GITHUB_API_ROOT}/repos/{POLICY_REPOSITORY}/releases/assets/{asset['id']}"
        )
        if asset.get("url") != expected_api_url:
            fail(f"policy {label} asset API URL is not exact")
        if (
            not isinstance(asset.get("size"), int)
            or isinstance(asset["size"], bool)
            or asset["size"] <= 0
        ):
            fail(f"policy {label} asset has no valid size")
        if not isinstance(asset.get("digest"), str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", asset["digest"]
        ):
            fail(f"policy {label} asset has no exact SHA-256 API digest")
    if wheel_asset["digest"] != f"sha256:{expected_wheel_sha256}":
        fail("policy wheel API digest differs from provenance.lock")
    if wheel_asset["size"] > MAX_FILE_BYTES:
        fail("policy wheel API size exceeds the image input limit")
    if digest_asset["size"] > MAX_DIGEST_JSON_BYTES:
        fail("policy digest JSON API size exceeds its limit")
    return wheel_asset, digest_asset


def validate_policy_digest(
    path: Path, release: str, source_commit: str, annotated_tag_sha: str
) -> None:
    try:
        with path.open("rb") as stream:
            raw = stream.read()
            if len(raw) > MAX_DIGEST_JSON_BYTES:
                raise ValueError("policy digest JSON exceeds its declared artifact size")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail(f"policy digest JSON is not valid: {exc}")
    expected_keys = {
        "schema_version",
        "image",
        "tag",
        "source_commit",
        "annotated_tag_sha",
        "digest",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        fail("policy digest JSON does not have the exact reviewed schema")
    if raw != (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode():
        fail("policy digest JSON is not compact canonical JSON")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        fail("policy digest JSON has an unsupported schema version")
    if payload["image"] != POLICY_IMAGE:
        fail("policy digest JSON has an unexpected image")
    if payload["tag"] != release:
        fail("policy digest JSON has an unexpected release tag")
    if payload["source_commit"] != source_commit:
        fail("policy digest JSON source commit differs from the annotated tag")
    if payload["annotated_tag_sha"] != annotated_tag_sha:
        fail("policy digest JSON tag object differs from the annotated tag ref")
    if not isinstance(payload["digest"], str) or not DIGEST_RE.fullmatch(payload["digest"]):
        fail("policy digest JSON does not contain an exact image digest")


def validate_provider_build_contract(path: Path, expected_root: str) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            names = []
            seen_names = set()
            seen_files = set()
            total_uncompressed_bytes = 0
            for member_number, member in enumerate(archive, 1):
                if member_number > MAX_ARCHIVE_MEMBERS:
                    fail(f"provider source archive has more than {MAX_ARCHIVE_MEMBERS} members")
                name = member.name
                if (
                    not name
                    or "\x00" in name
                    or any(character in BACKSLASH_CONFUSABLES for character in name)
                    or name.startswith("/")
                    or re.match(r"^[A-Za-z]:", name)
                    or unicodedata.normalize("NFC", name) != name
                ):
                    fail(f"provider source archive member path is unsafe: {name!r}")
                parts = name.split("/")
                if any(part in {"", ".", ".."} for part in parts):
                    fail(f"provider source archive member path is unsafe: {name!r}")
                try:
                    name_bytes = name.encode("utf-8")
                except UnicodeEncodeError as exc:
                    fail(f"provider source archive member path is not UTF-8: {exc}")
                if len(name_bytes) > MAX_ARCHIVE_NAME_BYTES:
                    fail(f"provider source archive member name exceeds {MAX_ARCHIVE_NAME_BYTES} bytes")
                if name in seen_names:
                    fail(f"provider source archive contains a duplicate member: {name}")
                if any("/".join(parts[:index]) in seen_files for index in range(1, len(parts))):
                    fail(f"provider source archive places a child below a regular file: {name}")
                if member.isdir():
                    if member.size != 0:
                        fail(f"provider source archive directory has nonzero size: {name}")
                elif member.isfile():
                    if member.size < 0:
                        fail("provider source archive contains a negative file size")
                    if any(existing.startswith(f"{name}/") for existing in seen_names):
                        fail(f"provider source archive makes a regular file a directory: {name}")
                    total_uncompressed_bytes += member.size
                    if total_uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        fail(
                            "provider source archive exceeds the "
                            f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES} byte uncompressed limit"
                        )
                    names.append(name)
                    seen_files.add(name)
                else:
                    fail(f"provider source archive contains a non-regular member: {name}")
                seen_names.add(name)
    except (OSError, EOFError, tarfile.TarError) as exc:
        fail(f"provider source archive is not readable: {exc}")

    setup_files = [name for name in names if name.endswith("/setup.py") or name == "setup.py"]
    pyproject_files = [
        name for name in names if name.endswith("/pyproject.toml") or name == "pyproject.toml"
    ]
    roots = {name.split("/", 1)[0] for name in seen_names}
    if len(roots) != 1:
        fail("provider source archive must contain exactly one project root")
    root = next(iter(roots))
    if root != expected_root or root not in seen_names or root in seen_files:
        fail("provider source archive project root is not an explicit directory")
    if setup_files != [f"{root}/setup.py"] or pyproject_files:
        fail(
            "provider source archive must expose exactly one setup.py and no pyproject.toml "
            "for the reviewed base setuptools build contract"
        )


def validate_synapse_fork_archive(path: Path, expected_root: str) -> None:
    """Validate the bounded, safe source archive used for the Synapse overlay."""

    names: set[str] = set()
    directories: set[str] = set()
    regular_files: set[str] = set()
    links: list[tuple[str, str]] = []
    total_uncompressed_bytes = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member_number, member in enumerate(archive, 1):
                if member_number > MAX_ARCHIVE_MEMBERS:
                    fail(f"Synapse fork archive has more than {MAX_ARCHIVE_MEMBERS} members")
                name = member.name
                if (
                    not name
                    or "\x00" in name
                    or any(character in BACKSLASH_CONFUSABLES for character in name)
                    or name.startswith("/")
                    or re.match(r"^[A-Za-z]:", name)
                    or unicodedata.normalize("NFC", name) != name
                ):
                    fail(f"Synapse fork archive member path is unsafe: {name!r}")
                parts = name.split("/")
                if any(part in {"", ".", ".."} for part in parts):
                    fail(f"Synapse fork archive member path is unsafe: {name!r}")
                if len(name.encode("utf-8")) > MAX_ARCHIVE_NAME_BYTES:
                    fail(f"Synapse fork archive member name exceeds {MAX_ARCHIVE_NAME_BYTES} bytes")
                if name in names:
                    fail(f"Synapse fork archive contains a duplicate member: {name}")
                if any("/".join(parts[:index]) in regular_files for index in range(1, len(parts))):
                    fail(f"Synapse fork archive places a child below a regular file: {name}")
                if member.isdir():
                    if member.size != 0:
                        fail("Synapse fork archive directory has nonzero size")
                    directories.add(name.removesuffix("/"))
                elif member.isfile():
                    if member.size < 0:
                        fail("Synapse fork archive contains a negative file size")
                    total_uncompressed_bytes += member.size
                    if total_uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        fail(
                            "Synapse fork archive exceeds the "
                            f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES} byte uncompressed limit"
                        )
                    regular_files.add(name)
                elif member.issym():
                    linkname = member.linkname
                    if not linkname or linkname.startswith("/") or "\\" in linkname:
                        fail(f"Synapse fork archive has an unsafe symlink: {name}")
                    links.append((name, linkname))
                else:
                    fail(f"Synapse fork archive contains a non-regular member: {name}")
                names.add(name)
    except (OSError, EOFError, tarfile.TarError) as exc:
        fail(f"Synapse fork archive is not readable: {exc}")

    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        fail("Synapse fork archive must contain exactly one project root")
    root = next(iter(roots))
    if root != expected_root or root not in directories or f"{root}/pyproject.toml" not in regular_files:
        fail("Synapse fork archive does not contain the expected project root")
    if f"{root}/synapse/__init__.py" not in regular_files:
        fail("Synapse fork archive does not contain the expected Synapse package")
    for name, linkname in links:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), linkname))
        if not target.startswith(f"{root}/") or target not in names:
            fail(f"Synapse fork archive symlink escapes its project root: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("s3-provider.lock"))
    parser.add_argument("--s3-provider-version", required=True)
    parser.add_argument("--synapse-fork-release", required=True)
    parser.add_argument("--synapse-fork-commit", required=True)
    parser.add_argument("--synapse-fork-archive-sha256", required=True)
    parser.add_argument("--s3-provider-fork-release", required=True)
    parser.add_argument("--s3-provider-fork-commit", required=True)
    parser.add_argument("--s3-provider-fork-archive-sha256", required=True)
    parser.add_argument("--policy-release", required=True)
    parser.add_argument("--policy-wheel-sha256", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not VERSION_RE.fullmatch(args.s3_provider_version):
        fail("S3 provider version is not an exact numeric release")
    if not VERSION_RE.fullmatch(args.policy_release):
        fail("policy release is not an exact numeric release")
    for name, value in (
        ("Synapse fork release", args.synapse_fork_release),
        ("S3-provider fork release", args.s3_provider_fork_release),
    ):
        if not FORK_RELEASE_RE.fullmatch(value):
            fail(f"{name} is not an exact release tag")
    for name, value in (
        ("Synapse fork commit", args.synapse_fork_commit),
        ("S3-provider fork commit", args.s3_provider_fork_commit),
    ):
        if not GIT_SHA_RE.fullmatch(value):
            fail(f"{name} is not an exact lowercase commit")
    for name, value in (
        ("Synapse fork archive", args.synapse_fork_archive_sha256),
        ("S3-provider fork archive", args.s3_provider_fork_archive_sha256),
        ("Policy wheel", args.policy_wheel_sha256),
    ):
        if not HEX_RE.fullmatch(value):
            fail(f"{name} SHA-256 must be lowercase hexadecimal")
    expected = load_lock(args.lock)
    if args.output.exists() and any(args.output.iterdir()):
        fail(f"output directory is not empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    wheelhouse = args.output / "wheelhouse"
    wheelhouse.mkdir()

    run_pip(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--no-deps",
            "--require-hashes",
            "--timeout",
            str(DOWNLOAD_TIMEOUT_SECONDS),
            "--index-url",
            "https://pypi.org/simple",
            "--dest",
            str(wheelhouse),
            "--requirement",
            str(args.lock),
        ]
    )
    validate_wheelhouse(wheelhouse, expected)

    synapse_archive_url, synapse_archive_root = fetch_fork_release(
        SYNAPSE_FORK_REPOSITORY,
        args.synapse_fork_release,
        args.synapse_fork_commit,
    )
    provider_archive_url, provider_archive_root = fetch_fork_release(
        S3_PROVIDER_FORK_REPOSITORY,
        args.s3_provider_fork_release,
        args.s3_provider_fork_commit,
    )
    synapse_archive = synapse_fork_archive_name(args.synapse_fork_release)
    download(
        synapse_archive_url,
        args.output / synapse_archive,
        args.synapse_fork_archive_sha256,
        expected_host="api.github.com",
        accept=GITHUB_API_ACCEPT,
    )
    validate_synapse_fork_archive(args.output / synapse_archive, synapse_archive_root)

    archive = s3_provider_fork_archive_name(args.s3_provider_fork_release)
    download(
        provider_archive_url,
        args.output / archive,
        args.s3_provider_fork_archive_sha256,
        expected_host="api.github.com",
        accept=GITHUB_API_ACCEPT,
    )
    validate_provider_build_contract(args.output / archive, provider_archive_root)

    wheel = f"telecrypt_tier_controller-{args.policy_release}-py3-none-any.whl"
    metadata = fetch_policy_release(args.policy_release)
    annotated_tag_sha, source_commit = fetch_policy_annotated_tag(args.policy_release)
    wheel_asset, digest_asset = validate_policy_assets(
        metadata, args.policy_release, wheel, args.policy_wheel_sha256
    )
    digest_name = digest_asset["name"]
    download(
        digest_asset["browser_download_url"],
        args.output / digest_name,
        digest_asset["digest"][len("sha256:") :],
        expected_size=digest_asset["size"],
    )
    validate_policy_digest(
        args.output / digest_name,
        args.policy_release,
        source_commit,
        annotated_tag_sha,
    )
    download(
        wheel_asset["browser_download_url"],
        args.output / wheel,
        args.policy_wheel_sha256,
        expected_size=wheel_asset["size"],
    )
    total_input_bytes = 0
    for item in args.output.rglob("*"):
        if item.is_symlink():
            fail(f"release inputs contain an unexpected symlink: {item}")
        if item.is_file():
            try:
                total_input_bytes += item.stat().st_size
            except OSError as exc:
                fail(f"could not stat release input {item}: {exc}")
    if total_input_bytes > MAX_TOTAL_INPUT_BYTES:
        fail(f"release inputs exceed the {MAX_TOTAL_INPUT_BYTES} byte total limit")
    print(f"prepared and verified {len(expected) + 4} exact image inputs")


if __name__ == "__main__":
    main()
