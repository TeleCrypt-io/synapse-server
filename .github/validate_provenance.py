#!/usr/bin/env python3
"""Validate the exact fork/source provenance used by the image workflow."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import validate_versions


REQUIRED_KEYS = (
    "SYNAPSE_UPSTREAM_TAG",
    "SYNAPSE_UPSTREAM_COMMIT",
    "SYNAPSE_FORK_RELEASE",
    "SYNAPSE_FORK_COMMIT",
    "SYNAPSE_FORK_ARCHIVE_SHA256",
    "S3_PROVIDER_UPSTREAM_TAG",
    "S3_PROVIDER_UPSTREAM_COMMIT",
    "S3_PROVIDER_FORK_RELEASE",
    "S3_PROVIDER_FORK_COMMIT",
    "S3_PROVIDER_FORK_ARCHIVE_SHA256",
    "POLICY_WHEEL_SHA256",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
TAG_RE = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
FORK_RELEASE_RE = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-telecrypt\.[1-9][0-9]*\Z"
)


def fail(message: str) -> None:
    raise SystemExit(f"provenance.lock: {message}")


def load_lock(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail("file is missing")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line != raw_line.strip() or raw_line.count("=") != 1:
            fail(f"line {line_number} must be KEY=value without surrounding whitespace")
        key, value = raw_line.split("=", 1)
        if key not in REQUIRED_KEYS:
            fail(f"line {line_number} has unknown key {key!r}")
        if key in values:
            fail(f"line {line_number} duplicates {key}")
        if not value:
            fail(f"line {line_number} has an empty value for {key}")
        values[key] = value
    missing = [key for key in REQUIRED_KEYS if key not in values]
    if missing:
        fail(f"missing required keys: {', '.join(missing)}")
    for key in (
        "SYNAPSE_UPSTREAM_COMMIT",
        "SYNAPSE_FORK_COMMIT",
        "S3_PROVIDER_UPSTREAM_COMMIT",
        "S3_PROVIDER_FORK_COMMIT",
    ):
        if not COMMIT_RE.fullmatch(values[key]):
            fail(f"{key} must be a lowercase 40-character commit")
    for key in (
        "SYNAPSE_FORK_ARCHIVE_SHA256",
        "S3_PROVIDER_FORK_ARCHIVE_SHA256",
        "POLICY_WHEEL_SHA256",
    ):
        if not SHA256_RE.fullmatch(values[key]):
            fail(f"{key} must be a lowercase SHA-256 digest")
    for key in (
        "SYNAPSE_UPSTREAM_TAG",
        "S3_PROVIDER_UPSTREAM_TAG",
        "SYNAPSE_FORK_RELEASE",
        "S3_PROVIDER_FORK_RELEASE",
    ):
        pattern = TAG_RE if key.endswith("UPSTREAM_TAG") else FORK_RELEASE_RE
        if not pattern.fullmatch(values[key]):
            fail(f"{key} must be an exact release tag")
    for release_key, upstream_key in (
        ("SYNAPSE_FORK_RELEASE", "SYNAPSE_UPSTREAM_TAG"),
        ("S3_PROVIDER_FORK_RELEASE", "S3_PROVIDER_UPSTREAM_TAG"),
    ):
        expected_prefix = f"{values[upstream_key]}-telecrypt."
        if not values[release_key].startswith(expected_prefix):
            fail(f"{release_key} must extend its exact upstream tag")
    if values["SYNAPSE_FORK_COMMIT"] == values["SYNAPSE_UPSTREAM_COMMIT"]:
        fail("Synapse fork commit must differ from its upstream base commit")
    if values["S3_PROVIDER_FORK_COMMIT"] == values["S3_PROVIDER_UPSTREAM_COMMIT"]:
        fail("S3-provider fork commit must differ from its upstream base commit")
    return values


def validate_against_versions(values: dict[str, str], versions_path: Path) -> None:
    versions = validate_versions.load_versions(versions_path)
    if values["SYNAPSE_UPSTREAM_TAG"] != f"v{versions['SYNAPSE_VERSION']}":
        fail("Synapse upstream tag differs from versions.env")
    if values["S3_PROVIDER_UPSTREAM_TAG"] != f"v{versions['S3_PROVIDER_VERSION']}":
        fail("S3-provider upstream tag differs from versions.env")


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: validate_provenance.py provenance.lock [versions.env]")
    values = load_lock(Path(sys.argv[1]))
    validate_against_versions(values, Path(sys.argv[2]) if len(sys.argv) == 3 else Path("versions.env"))
    for key in REQUIRED_KEYS:
        print(f"{key}={values[key]}")


if __name__ == "__main__":
    main()
