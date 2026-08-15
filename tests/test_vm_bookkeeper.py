import importlib.util
import json
import stat
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vm_bookkeeper", ROOT / "scripts" / "vm_bookkeeper.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load vm_bookkeeper module")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / "vms.json"
        self.private = self.root / "id_ed25519"
        self.public = self.root / "id_ed25519.pub"
        self.private.write_text("test-private-placeholder")
        self.public.write_text("ssh-ed25519 AAAATEST test")

    def upsert_args(self, provider: str = "aws", vm_id: str = "i-test", location: str = "us-east-1") -> Namespace:
        return Namespace(
            registry=str(self.registry), provider=provider, id=vm_id, name="dev",
            location=location, machine_type="test-type", public_ip="192.0.2.1",
            ssh_user="ubuntu", private_key=str(self.private), public_key=str(self.public),
            cloud_key_name="remote-computer-dev", status="running",
        )

    def test_init_creates_private_valid_registry(self):
        rc = vm.cmd_init(Namespace(registry=str(self.registry)))
        self.assertEqual(rc, 0)
        data = json.loads(self.registry.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["vms"], [])
        self.assertEqual(stat.S_IMODE(self.registry.stat().st_mode), 0o600)

    def test_upsert_preserves_created_at(self):
        vm.cmd_upsert(self.upsert_args())
        first = json.loads(self.registry.read_text())["vms"][0]
        args = self.upsert_args()
        args.status = "stopped"
        vm.cmd_upsert(args)
        records = json.loads(self.registry.read_text())["vms"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["created_at"], first["created_at"])
        self.assertEqual(records[0]["status"], "stopped")
        self.assertNotIn("test-private-placeholder", self.registry.read_text())

    def test_invalid_registry_is_not_overwritten(self):
        self.registry.write_text("not json")
        with self.assertRaises(ValueError):
            vm.load_registry(self.registry)
        self.assertEqual(self.registry.read_text(), "not json")

    def test_sync_updates_aws_and_gcp_from_mocked_clis(self):
        vm.cmd_upsert(self.upsert_args())
        vm.cmd_upsert(self.upsert_args(provider="gcp", vm_id="gcp-dev", location="us-central1-a"))
        aws_payload = {"Reservations": [{"Instances": [{"State": {"Name": "stopped"}, "PublicIpAddress": "192.0.2.10"}]}]}
        gcp_payload = {"status": "RUNNING", "networkInterfaces": [{"accessConfigs": [{"natIP": "192.0.2.20"}]}]}
        with mock.patch.object(vm, "command_path", return_value="/mock/bin"), mock.patch.object(vm, "run_json", side_effect=[aws_payload, gcp_payload]):
            rc = vm.cmd_sync(Namespace(registry=str(self.registry)))
        self.assertEqual(rc, 0)
        records = {(x["provider"], x["id"]): x for x in json.loads(self.registry.read_text())["vms"]}
        self.assertEqual(records[("aws", "i-test")]["status"], "stopped")
        self.assertEqual(records[("aws", "i-test")]["public_ip"], "192.0.2.10")
        self.assertEqual(records[("gcp", "gcp-dev")]["status"], "running")
        self.assertEqual(records[("gcp", "gcp-dev")]["public_ip"], "192.0.2.20")

    def test_sync_error_preserves_previous_record(self):
        vm.cmd_upsert(self.upsert_args())
        before = json.loads(self.registry.read_text())["vms"][0]
        with mock.patch.object(vm, "command_path", return_value="/mock/aws"), mock.patch.object(vm, "run_json", side_effect=RuntimeError("denied")):
            rc = vm.cmd_sync(Namespace(registry=str(self.registry)))
        after = json.loads(self.registry.read_text())["vms"][0]
        self.assertEqual(rc, 1)
        self.assertEqual(after, before)

    def test_clipboard_fallback_prints_command(self):
        with mock.patch.object(vm, "clipboard_tool", return_value=None), mock.patch("builtins.print") as output:
            rc = vm.cmd_copy_ssh(Namespace(command="ssh example"))
        self.assertEqual(rc, 2)
        self.assertTrue(any(call.args and call.args[0] == "ssh example" for call in output.call_args_list))

    @unittest.skipUnless(Path("/usr/bin/ssh-keygen").exists() or Path("/bin/ssh-keygen").exists(), "ssh-keygen unavailable")
    def test_create_key_generates_unencrypted_pair_without_overwrite(self):
        key_path = self.root / "keys" / "dev"
        rc = vm.cmd_create_key(Namespace(name="dev", path=str(key_path)))
        self.assertEqual(rc, 0)
        self.assertTrue(key_path.is_file())
        self.assertTrue(Path(f"{key_path}.pub").is_file())
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            vm.cmd_create_key(Namespace(name="dev", path=str(key_path)))


if __name__ == "__main__":
    unittest.main()
