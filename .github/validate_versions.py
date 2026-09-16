#!/usr/bin/env python3
"""Validate the checked-in image component versions and emit workflow outputs."""

from __future__ import annotations

import re
import sys
from pathlib import Path


VERSION_KEYS = (
    "SYNAPSE_VERSION",
    "TELECRYPT_REVISION",
    "S3_PROVIDER_VERSION",
    "POLICY_RELEASE",
)
ALL_KEYS = VERSION_KEYS
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
REVISION_RE = re.compile(r"[1-9][0-9]*\Z")


def fail(message: str) -> None:
    raise SystemExit(f"versions.env: {message}")


def load_versions(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail("file is missing")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line != raw_line.strip() or raw_line.count("=") != 1:
            fail(f"line {line_number} must be KEY=value without surrounding whitespace")
        key, value = raw_line.split("=", 1)
        if key not in ALL_KEYS:
            fail(f"line {line_number} has unknown key {key!r}")
        if key in values:
            fail(f"line {line_number} duplicates {key}")
        if not value:
            fail(f"line {line_number} has an empty value for {key}")
        values[key] = value

    missing = [key for key in ALL_KEYS if key not in values]
    if missing:
        fail(f"missing required keys: {', '.join(missing)}")
    for key in VERSION_KEYS:
        value = values[key]
        pattern = REVISION_RE if key == "TELECRYPT_REVISION" else VERSION_RE
        if not pattern.fullmatch(value):
            fail(f"{key} must be an exact numeric release, got {value!r}")
    return values


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_versions.py versions.env")
    values = load_versions(Path(sys.argv[1]))
    image_tag = f"{values['SYNAPSE_VERSION'].rsplit('.', 1)[0]}-tc{values['TELECRYPT_REVISION']}"
    for key in (*ALL_KEYS, "IMAGE_TAG"):
        print(f"{key}={values[key] if key in values else image_tag}")


if __name__ == "__main__":
    main()
