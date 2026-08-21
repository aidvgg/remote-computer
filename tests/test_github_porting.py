from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


book = load_module("vm_bookkeeper", SKILL_ROOT / "scripts" / "vm_bookkeeper.py")
remote = load_module("remote_github_setup", SKILL_ROOT / "scripts" / "remote_github_setup.py")


def make_key(path: Path, comment: str = "test@example") -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
        check=True,
    )


class RegistryCompatibilityTests(unittest.TestCase):
    def test_legacy_registry_without_github_field_remains_valid(self) -> None:
        legacy: dict[str, object] = {
            "version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "vms": [],
        }
        validated = book.validate_registry(legacy)
        self.assertNotIn("github_auth", validated)

    def test_invalid_optional_github_field_is_rejected(self) -> None:
        registry = cast(dict[str, object], book.empty_registry())
        registry["github_auth"] = {"not": "an array"}
        with self.assertRaisesRegex(ValueError, "github_auth"):
            book.validate_registry(registry)


class GithubKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / "registry.json"
        book.save_registry(self.registry, book.empty_registry())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_shared_key_preserves_vm_shape_and_stores_metadata_only(self) -> None:
        key_path = self.root / "shared-key"
        args = argparse.Namespace(
            registry=str(self.registry),
            host="github.com",
            account="octocat",
            title="remote-computer shared octocat",
            path=str(key_path),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(book.cmd_github_create_key(args), 0)
        data = json.loads(self.registry.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["vms"], [])
        self.assertEqual(data["github_auth"][0]["account"], "octocat")
        self.assertEqual(data["github_auth"][0]["source"], "generated")
        self.assertEqual(data["github_auth"][0]["installed_on"], [])
        self.assertNotIn(key_path.read_text(), self.registry.read_text())
        self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(f"{key_path}.pub").st_mode & 0o777, 0o644)

    def test_register_refuses_mismatched_key_pair(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        make_key(first, "first")
        make_key(second, "second")
        args = argparse.Namespace(
            registry=str(self.registry),
            host="github.com",
            account="octocat",
            private_key=str(first),
            public_key=f"{second}.pub",
            title="mismatch",
        )
        with self.assertRaisesRegex(ValueError, "do not match"):
            book.cmd_github_register_key(args)

    def test_github_ssh_success_greeting_extracts_account(self) -> None:
        greeting = "Hi octocat! You've successfully authenticated, but GitHub does not provide shell access."
        self.assertEqual(book.github_ssh_login(greeting), "octocat")
        self.assertIsNone(book.github_ssh_login("Permission denied (publickey)."))

    @mock.patch.object(book, "command_path", return_value="/usr/bin/gh")
    @mock.patch.object(book, "run_text")
    def test_gh_status_drops_unexpected_token_fields(
        self,
        run_text: mock.MagicMock,
        _command_path: mock.MagicMock,
    ) -> None:
        run_text.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "hosts": {
                    "github.com": [{
                        "login": "octocat",
                        "active": True,
                        "state": "success",
                        "gitProtocol": "ssh",
                        "token": "must-not-escape",
                    }]
                }
            }),
            stderr="",
        )
        status = book.gh_status("github.com")
        self.assertNotIn("must-not-escape", json.dumps(status))

    def test_store_refuses_implicit_key_rotation(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        make_key(first, "first")
        make_key(second, "second")
        data = book.load_registry(self.registry)
        book.store_github_record(
            data,
            "github.com",
            "octocat",
            first,
            Path(f"{first}.pub"),
            "discovered",
            book.ssh_fingerprint(Path(f"{first}.pub")),
            "first",
        )
        with self.assertRaisesRegex(ValueError, "refusing to replace"):
            book.store_github_record(
                data,
                "github.com",
                "octocat",
                second,
                Path(f"{second}.pub"),
                "discovered",
                book.ssh_fingerprint(Path(f"{second}.pub")),
                "second",
            )

    @mock.patch.object(book, "checked_text", return_value="")
    @mock.patch.object(book, "github_registered_fingerprints")
    @mock.patch.object(book, "gh_status")
    def test_upload_uses_public_key_and_verifies_fingerprint(
        self,
        status: mock.MagicMock,
        registered: mock.MagicMock,
        checked: mock.MagicMock,
    ) -> None:
        key_path = self.root / "shared-key"
        make_key(key_path, "shared")
        data = book.load_registry(self.registry)
        fingerprint = book.run_text(["ssh-keygen", "-lf", f"{key_path}.pub"]).stdout.split()[1]
        book.store_github_record(
            data,
            "github.com",
            "octocat",
            key_path,
            Path(f"{key_path}.pub"),
            "generated",
            fingerprint,
            "shared title",
        )
        book.save_registry(self.registry, data)
        status.return_value = {
            "path": "/usr/bin/gh",
            "accounts": [{"login": "octocat", "active": True, "state": "success"}],
        }
        registered.side_effect = [(set(), "ok"), ({fingerprint}, "ok")]
        def checked_side_effect(command: list[str], **_kwargs: object) -> str:
            return book.run_text(command).stdout.strip() if command[0] == "ssh-keygen" else ""

        checked.side_effect = checked_side_effect
        args = argparse.Namespace(registry=str(self.registry), host="github.com", account="octocat")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(book.cmd_github_upload_key(args), 0)
        command = cast(list[str], checked.call_args.args[0])
        resolved_public = str(Path(f"{key_path}.pub").resolve())
        self.assertIn(resolved_public, command)
        self.assertNotIn(str(key_path.resolve()), command)

    @mock.patch.object(book, "gh_status")
    def test_upload_refuses_wrong_gh_account(self, status: mock.MagicMock) -> None:
        key_path = self.root / "shared-key"
        make_key(key_path, "shared")
        data = book.load_registry(self.registry)
        book.store_github_record(
            data,
            "github.com",
            "octocat",
            key_path,
            Path(f"{key_path}.pub"),
            "generated",
            book.ssh_fingerprint(Path(f"{key_path}.pub")),
            "shared title",
        )
        book.save_registry(self.registry, data)
        status.return_value = {
            "path": "/usr/bin/gh",
            "accounts": [{"login": "someone-else", "active": True, "state": "success"}],
        }
        args = argparse.Namespace(registry=str(self.registry), host="github.com", account="octocat")
        with self.assertRaisesRegex(RuntimeError, "octocat"):
            book.cmd_github_upload_key(args)


class RemoteSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_managed_ssh_block_preserves_unrelated_config_and_is_idempotent(self) -> None:
        config = self.root / "config"
        config.write_text("Host example.org\n  User deploy\n")
        private = self.root / "github-key"
        remote.update_ssh_config(config, "github.com", "octocat", private)
        first = config.read_text()
        remote.update_ssh_config(config, "github.com", "octocat", private)
        second = config.read_text()
        self.assertEqual(first, second)
        self.assertIn("Host github.com", second)
        self.assertIn("Host example.org", second)
        self.assertEqual(second.count("BEGIN remote-computer"), 1)
        self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)

    def test_unmanaged_github_host_block_is_not_overwritten(self) -> None:
        config = self.root / "config"
        config.write_text("Host github.com\n  IdentityFile ~/.ssh/personal\n")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            remote.update_ssh_config(config, "github.com", "octocat", self.root / "new-key")


class GithubInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / "registry.json"
        self.github_key = self.root / "github-key"
        self.vm_key = self.root / "vm-key"
        make_key(self.github_key, "github")
        make_key(self.vm_key, "vm")
        timestamp = "2026-01-01T00:00:00+00:00"
        data = book.empty_registry()
        data["vms"] = [{
            "provider": "aws",
            "id": "i-test",
            "name": "test",
            "location": "us-east-1",
            "machine_type": "t3.medium",
            "public_ip": "203.0.113.10",
            "ssh_user": "ubuntu",
            "key_pair": {
                "private_path": str(self.vm_key),
                "public_path": f"{self.vm_key}.pub",
                "cloud_name": "test",
            },
            "status": "running",
            "created_at": timestamp,
            "checked_at": timestamp,
        }]
        book.store_github_record(
            data,
            "github.com",
            "octocat",
            self.github_key,
            Path(f"{self.github_key}.pub"),
            "generated",
            book.ssh_fingerprint(Path(f"{self.github_key}.pub")),
            "shared",
        )
        book.save_registry(self.registry, data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @mock.patch.object(book, "command_path", return_value="/usr/bin/tool")
    @mock.patch.object(book, "checked_subprocess")
    def test_install_records_vm_only_after_remote_verification(
        self,
        checked: mock.MagicMock,
        _command_path: mock.MagicMock,
    ) -> None:
        expected = book.ssh_fingerprint(Path(f"{self.github_key}.pub"))
        checked.side_effect = ["", "", "", "", json.dumps({
            "account": "octocat",
            "host": "github.com",
            "fingerprint": expected,
        })]
        args = argparse.Namespace(
            registry=str(self.registry),
            host="github.com",
            account="octocat",
            provider="aws",
            id="i-test",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(book.cmd_github_install_key(args), 0)
        data = book.load_registry(self.registry)
        installs = data["github_auth"][0]["installed_on"]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0]["id"], "i-test")
        self.assertTrue(installs[0]["remote_private_path"].startswith("~/.ssh/remote-computer/github/"))
        self.assertEqual(checked.call_count, 5)

    @mock.patch.object(book, "command_path", return_value="/usr/bin/tool")
    @mock.patch.object(book, "checked_subprocess", side_effect=RuntimeError("remote setup failed"))
    def test_install_failure_does_not_claim_registry_success(
        self,
        _checked: mock.MagicMock,
        _command_path: mock.MagicMock,
    ) -> None:
        args = argparse.Namespace(
            registry=str(self.registry),
            host="github.com",
            account="octocat",
            provider="aws",
            id="i-test",
        )
        with self.assertRaisesRegex(RuntimeError, "remote setup failed"):
            book.cmd_github_install_key(args)
        data = book.load_registry(self.registry)
        self.assertEqual(data["github_auth"][0]["installed_on"], [])


if __name__ == "__main__":
    unittest.main()
