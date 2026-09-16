#!/usr/bin/env python3
"""Focused tests for the exact fork/source provenance lock."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import prepare_inputs
import validate_provenance


ROOT = Path(__file__).resolve().parents[1]


class ProvenanceTests(unittest.TestCase):
    def _synapse_archive(
        self,
        path: Path,
        commit: str,
        *,
        unsafe_link: bool = False,
        second_root: bool = False,
    ) -> None:
        root = f"TeleCrypt-io-fork-synapse-{commit[:7]}"
        with tarfile.open(path, mode="w:gz") as archive:
            for name in (f"{root}/", f"{root}/synapse/"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name, body in ((f"{root}/pyproject.toml", b"[project]\n"), (f"{root}/synapse/__init__.py", b"")):
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            link = tarfile.TarInfo(f"{root}/safe-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "synapse/__init__.py" if not unsafe_link else "../../outside"
            archive.addfile(link)
            if second_root:
                member = tarfile.TarInfo("unexpected/")
                member.type = tarfile.DIRTYPE
                archive.addfile(member)

    def test_checked_in_provenance_matches_versions(self) -> None:
        values = validate_provenance.load_lock(ROOT / "provenance.lock")
        validate_provenance.validate_against_versions(values, ROOT / "versions.env")

    def test_rejects_unknown_duplicate_or_malformed_fields(self) -> None:
        valid = (ROOT / "provenance.lock").read_text(encoding="ascii")
        mutations = (
            valid + "UNKNOWN=value\n",
            valid + "SYNAPSE_FORK_COMMIT=" + "a" * 40 + "\n",
            valid.replace("SYNAPSE_FORK_COMMIT=", "SYNAPSE_FORK_COMMIT=not-a-commit", 1),
            valid.replace("SYNAPSE_FORK_RELEASE=", "SYNAPSE_FORK_RELEASE=v1.159.0-telecrypt.0 #", 1),
            valid.replace("SYNAPSE_FORK_ARCHIVE_SHA256=", "SYNAPSE_FORK_ARCHIVE_SHA256=bad", 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provenance.lock"
            for mutation in mutations:
                path.write_text(mutation, encoding="ascii")
                with self.subTest(mutation=mutation), self.assertRaises(SystemExit):
                    validate_provenance.load_lock(path)

    def test_rejects_version_drift(self) -> None:
        values = validate_provenance.load_lock(ROOT / "provenance.lock")
        with tempfile.TemporaryDirectory() as directory:
            versions = Path(directory) / "versions.env"
            original = (ROOT / "versions.env").read_text(encoding="ascii")
            versions.write_text(original.replace("SYNAPSE_VERSION=1.159.0", "SYNAPSE_VERSION=1.158.0"), encoding="ascii")
            with self.assertRaises(SystemExit):
                validate_provenance.validate_against_versions(values, versions)

    def test_synapse_archive_validator_accepts_safe_links_and_rejects_escape(self) -> None:
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synapse.tar.gz"
            root = f"TeleCrypt-io-fork-synapse-{commit[:7]}"
            self._synapse_archive(path, commit)
            prepare_inputs.validate_synapse_fork_archive(path, root)
            self._synapse_archive(path, commit, unsafe_link=True)
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_synapse_fork_archive(path, root)
            self._synapse_archive(path, commit, second_root=True)
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_synapse_fork_archive(path, root)
            self._synapse_archive(path, commit)
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_synapse_fork_archive(
                    path, "TeleCrypt-io-fork-synapse-bbbbbbb"
                )

    def test_archive_names_use_locked_releases(self) -> None:
        values = validate_provenance.load_lock(ROOT / "provenance.lock")
        synapse_name = f"synapse-{values['SYNAPSE_FORK_RELEASE']}.tar.gz"
        provider_name = f"synapse-s3-storage-provider-{values['S3_PROVIDER_FORK_RELEASE']}.tar.gz"
        self.assertEqual(
            prepare_inputs.synapse_fork_archive_name(values["SYNAPSE_FORK_RELEASE"]),
            synapse_name,
        )
        self.assertEqual(
            prepare_inputs.s3_provider_fork_archive_name(values["S3_PROVIDER_FORK_RELEASE"]),
            provider_name,
        )


if __name__ == "__main__":
    unittest.main()
