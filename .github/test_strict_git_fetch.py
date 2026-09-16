#!/usr/bin/env python3
"""Semantic tests for the Synapse release Git boundary."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parent / "strict_git_fetch.sh"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class StrictGitFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="synapse-git-")
        self.root = Path(self.directory.name)
        git(self.root, "init", "--quiet")
        (self.root / "README").write_text("fixture\n", encoding="utf-8")
        git(self.root, "add", "README")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "--quiet", "-m", "fixture")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_helper(self, *args: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), *args],
            cwd=self.root,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_local_identity_ignores_ambient_git_configuration(self) -> None:
        expected = git(self.root, "rev-parse", "HEAD")
        result = self.run_helper(
            "local-read", "rev-parse", "HEAD",
            GIT_DIR=str(self.root / "missing"),
            GIT_INDEX_FILE=str(self.root / "missing-index"),
            GIT_OBJECT_DIRECTORY=str(self.root / "missing-objects"),
            GIT_REPLACE_REF_BASE="refs/replace/hostile",
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.proxy",
            GIT_CONFIG_VALUE_0="http://evil.invalid",
            GIT_TRACE="/tmp/synapse-hostile-trace",
            GIT_TRACE2="/tmp/synapse-hostile-trace2",
            GIT_TRACE_PACK_ACCESS="1",
            GIT_TRACE_PERFORMANCE="1",
            GIT_TRACE_PACKET="1",
            GIT_TRACE_SHALLOW="1",
            GIT_CURL_VERBOSE="1",
            GIT_TRACE2_ENV_VARS="GIT_DIR",
            GIT_TRACE2_MAX_FILES="1",
            HTTPS_PROXY="http://evil.invalid",
            GIT_ALLOW_PROTOCOL="file:ext:ssh",
            GIT_PROTOCOL_FROM_USER="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)

    def test_annotated_tag_reads_are_unreplaced(self) -> None:
        first_commit = git(self.root, "rev-parse", "HEAD")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "tag", "-a", "v1", "-m", "v1")
        first_tag = git(self.root, "rev-parse", "refs/tags/v1")
        (self.root / "README").write_text("changed\n", encoding="utf-8")
        git(self.root, "add", "README")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "--quiet", "-m", "changed")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "tag", "-a", "v2", "-m", "v2")
        git(self.root, "replace", first_tag, git(self.root, "rev-parse", "refs/tags/v2"))
        raw = self.run_helper("local-read", "rev-parse", "refs/tags/v1")
        peeled = self.run_helper("local-read", "rev-parse", "refs/tags/v1^{}")
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(peeled.returncode, 0, peeled.stderr)
        self.assertEqual(raw.stdout.strip(), first_tag)
        self.assertEqual(peeled.stdout.strip(), first_commit)

    def test_real_annotated_tag_binds_object_and_peeled_commit(self) -> None:
        commit = git(self.root, "rev-parse", "HEAD")
        git(
            self.root,
            "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
            "tag", "-a", "1.159-tc3", "-m", "release",
        )
        tag_object = git(self.root, "rev-parse", "refs/tags/1.159-tc3")
        raw = self.run_helper("local-read", "rev-parse", "refs/tags/1.159-tc3")
        peeled = self.run_helper("local-read", "rev-parse", "refs/tags/1.159-tc3^{}")
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(peeled.returncode, 0, peeled.stderr)
        self.assertEqual(raw.stdout.strip(), tag_object)
        self.assertEqual(peeled.stdout.strip(), commit)
        self.assertNotEqual(tag_object, commit)

    def test_refspec_and_repository_arguments_are_narrow(self) -> None:
        self.assertNotEqual(self.run_helper("--upload-pack=/tmp/hostile").returncode, 0)
        self.assertNotEqual(self.run_helper("fetch", "--upload-pack=/tmp/hostile").returncode, 0)
        self.assertNotEqual(self.run_helper("local-read", "rev-parse", "--option").returncode, 0)
        self.assertNotEqual(self.run_helper("fetch", "refs/tags/v1^{commit}:refs/tags/v1").returncode, 0)

    def test_rejects_repository_local_transport_configuration(self) -> None:
        for key, value in (
            ("url.hostile.insteadOf", "https://github.com/"),
            ("url.hostile.pushInsteadOf", "https://github.com/"),
            ("include.path", str(self.root / "included-config")),
            ("includeIf.onbranch:main.path", str(self.root / "included-config")),
            ("credential.helper", "store"),
            ("hooks.allownonstdhook", "true"),
            ("core.hooksPath", str(self.root / "hooks")),
            ("remote.origin.vcs", "hostile-helper"),
            ("remote.origin.proxy", "http://evil.invalid"),
            ("remote.origin.uploadpack", "/tmp/hostile-upload-pack"),
            ("remote.origin.receivepack", "/tmp/hostile-receive-pack"),
            ("remote.origin.pushurl", "https://evil.invalid/repository.git"),
            ("remote.evil.vcs", "hostile-helper"),
            ("remote.evil.pushurl", "https://evil.invalid/repository.git"),
            ("remote.evil.url", "https://evil.invalid/repository.git"),
        ):
            git(self.root, "config", "--local", key, value)
            result = self.run_helper("local-read", "rev-parse", "HEAD")
            self.assertNotEqual(result.returncode, 0, key)
            git(self.root, "config", "--local", "--unset-all", key)

    def test_check_is_a_real_git_operation(self) -> None:
        result = self.run_helper("check", GIT_CONFIG_SYSTEM="/tmp/hostile", GIT_CONFIG_GLOBAL="/tmp/hostile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_git_failure_surfaces_its_real_diagnostics(self) -> None:
        (self.root / ".git" / "HEAD").write_text(
            "ref: refs/heads/missing\n", encoding="ascii"
        )
        result = self.run_helper("local-read", "rev-parse", "HEAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fatal:", result.stderr)
        self.assertNotIn("output exceeded", result.stderr)

    def test_accepts_github_canonical_remote_with_or_without_git_suffix(self) -> None:
        for remote in (
            "https://github.com/TeleCrypt-io/synapse-server.git",
            "https://github.com/TeleCrypt-io/synapse-server",
        ):
            git(self.root, "config", "--local", "remote.origin.url", remote)
            result = self.run_helper("local-read", "rev-parse", "HEAD")
            self.assertEqual(result.returncode, 0, result.stderr)
            git(self.root, "config", "--local", "--unset-all", "remote.origin.url")


if __name__ == "__main__":
    unittest.main()
