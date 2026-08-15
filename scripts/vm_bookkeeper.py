#!/usr/bin/env python3
"""Safe local bookkeeping helpers for the remote-computer skill."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast


class KeyPair(TypedDict):
    private_path: str
    public_path: str
    cloud_name: str


class VMRecord(TypedDict):
    provider: str
    id: str
    name: str
    location: str
    machine_type: str
    public_ip: str
    ssh_user: str
    key_pair: KeyPair
    status: str
    created_at: str
    checked_at: str


class Registry(TypedDict):
    version: int
    created_at: str
    updated_at: str
    vms: list[VMRecord]


SCHEMA_VERSION = 1
VALID_PROVIDERS = {"aws", "gcp"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def registry_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else Path.home() / ".vms.json"


def empty_registry() -> Registry:
    timestamp = now()
    return {"version": SCHEMA_VERSION, "created_at": timestamp, "updated_at": timestamp, "vms": []}


def validate_registry(data: object) -> Registry:
    if not isinstance(data, dict):
        raise ValueError(f"registry must be an object with version {SCHEMA_VERSION}")
    raw = cast(dict[str, object], data)
    if raw.get("version") != SCHEMA_VERSION:
        raise ValueError(f"registry must be an object with version {SCHEMA_VERSION}")
    raw_vms = raw.get("vms")
    if not isinstance(raw_vms, list):
        raise ValueError("registry field 'vms' must be an array")
    for raw_item in cast(list[object], raw_vms):
        if not isinstance(raw_item, dict):
            raise ValueError("every VM record must be an object")
        item = cast(dict[str, object], raw_item)
        provider = item.get("provider")
        vm_id = item.get("id")
        if provider not in VALID_PROVIDERS or not isinstance(vm_id, str) or not vm_id:
            raise ValueError("every VM needs a valid provider and non-empty id")
    return cast(Registry, raw)


def load_registry(path: Path, create: bool = False) -> Registry:
    if not path.exists():
        if create:
            data = empty_registry()
            save_registry(path, data)
            return data
        raise FileNotFoundError(f"registry does not exist: {path}")
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
        return validate_registry(parsed)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in registry {path}: {exc}") from exc


def save_registry(path: Path, data: Registry) -> None:
    validate_registry(data)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data["updated_at"] = now()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def command_path(name: str) -> str | None:
    return shutil.which(name)


def clipboard_tool() -> tuple[str, list[str]] | None:
    system = platform.system()
    if system == "Darwin" and command_path("pbcopy"):
        return "pbcopy", ["pbcopy"]
    if system == "Linux":
        if os.environ.get("WAYLAND_DISPLAY") and command_path("wl-copy"):
            return "wl-copy", ["wl-copy"]
        if os.environ.get("DISPLAY") and command_path("xclip"):
            return "xclip", ["xclip", "-selection", "clipboard"]
        if os.environ.get("DISPLAY") and command_path("xsel"):
            return "xsel", ["xsel", "--clipboard", "--input"]
    return None


def run_json(command: list[str]) -> dict[str, object]:
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=45)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(message)
    try:
        value: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider CLI returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("provider CLI returned a non-object JSON value")
    return cast(dict[str, object], value)


def object_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError(f"provider CLI field {field!r} is not an array")
    return cast(list[object], value)


def object_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"provider CLI field {field!r} is not an object")
    return cast(dict[str, object], value)


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def aws_state(record: VMRecord) -> tuple[str, str | None]:
    payload = run_json([
        "aws", "ec2", "describe-instances", "--instance-ids", record["id"],
        "--region", record["location"], "--output", "json",
    ])
    reservations = object_list(payload.get("Reservations", []), "Reservations")
    if not reservations:
        return "missing", None
    reservation = object_dict(reservations[0], "Reservations[0]")
    instances = object_list(reservation.get("Instances", []), "Instances")
    if not instances:
        return "missing", None
    instance = object_dict(instances[0], "Instances[0]")
    state = object_dict(instance.get("State", {}), "State")
    state_name = optional_string(state.get("Name")) or "unknown"
    return state_name, optional_string(instance.get("PublicIpAddress"))


def gcp_state(record: VMRecord) -> tuple[str, str | None]:
    payload = run_json([
        "gcloud", "compute", "instances", "describe", record["id"],
        "--zone", record["location"], "--format=json",
    ])
    status = (optional_string(payload.get("status")) or "unknown").lower()
    interfaces = object_list(payload.get("networkInterfaces", []), "networkInterfaces")
    if not interfaces:
        return status, None
    interface = object_dict(interfaces[0], "networkInterfaces[0]")
    access_configs = object_list(interface.get("accessConfigs", []), "accessConfigs")
    if not access_configs:
        return status, None
    access_config = object_dict(access_configs[0], "accessConfigs[0]")
    return status, optional_string(access_config.get("natIP"))


def cmd_doctor(args: argparse.Namespace) -> int:
    clip = clipboard_tool()
    result = {
        "platform": platform.system().lower(),
        "aws": {"installed": bool(command_path("aws")), "path": command_path("aws")},
        "gcp": {"installed": bool(command_path("gcloud")), "path": command_path("gcloud")},
        "ssh_keygen": {"installed": bool(command_path("ssh-keygen")), "path": command_path("ssh-keygen")},
        "clipboard": {"available": bool(clip), "tool": clip[0] if clip else None},
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = registry_path(args.registry)
    created = not path.exists()
    data = load_registry(path, create=True)
    print(json.dumps({"created": created, "path": str(path), "vm_count": len(data["vms"])}, sort_keys=True))
    return 0


def cmd_create_key(args: argparse.Namespace) -> int:
    if not NAME_RE.fullmatch(args.name):
        raise ValueError("key name must use 1-63 letters, digits, dots, underscores, or hyphens")
    if not command_path("ssh-keygen"):
        raise RuntimeError("ssh-keygen is required")
    private = Path(args.path).expanduser() if args.path else Path.home() / ".ssh" / "remote-computer" / args.name
    public = Path(f"{private}.pub")
    if private.exists() or public.exists():
        raise FileExistsError(f"refusing to overwrite existing key: {private}")
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private.parent, 0o700)
    result = subprocess.run([
        "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"remote-computer:{args.name}", "-f", str(private)
    ], text=True, capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ssh-keygen failed")
    os.chmod(private, 0o600)
    os.chmod(public, 0o644)
    print(json.dumps({"private_key": str(private), "public_key": str(public)}, sort_keys=True))
    return 0


def cmd_upsert(args: argparse.Namespace) -> int:
    path = registry_path(args.registry)
    data = load_registry(path, create=True)
    private = Path(args.private_key).expanduser()
    public = Path(args.public_key).expanduser()
    if not private.is_file() or not public.is_file():
        raise FileNotFoundError("private and public key paths must both exist")
    timestamp = now()
    provider = str(args.provider)
    vm_id = str(args.id)
    index = next((i for i, vm in enumerate(data["vms"]) if vm["provider"] == provider and vm["id"] == vm_id), None)
    created_at = timestamp if index is None else data["vms"][index]["created_at"]
    record: VMRecord = {
        "provider": provider,
        "id": vm_id,
        "name": str(args.name),
        "location": str(args.location),
        "machine_type": str(args.machine_type),
        "public_ip": str(args.public_ip),
        "ssh_user": str(args.ssh_user),
        "key_pair": {
            "private_path": str(private.resolve()),
            "public_path": str(public.resolve()),
            "cloud_name": str(args.cloud_key_name),
        },
        "status": str(args.status),
        "created_at": created_at,
        "checked_at": timestamp,
    }
    if index is None:
        data["vms"].append(record)
        action = "created"
    else:
        data["vms"][index] = record
        action = "updated"
    save_registry(path, data)
    print(json.dumps({"action": action, "provider": provider, "id": vm_id, "path": str(path)}, sort_keys=True))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    path = registry_path(args.registry)
    data = load_registry(path, create=False)
    results: list[dict[str, object]] = []
    changed = False
    for record in data["vms"]:
        provider = record["provider"]
        executable = "aws" if provider == "aws" else "gcloud"
        if not command_path(executable):
            results.append({"provider": provider, "id": record["id"], "result": "skipped", "reason": f"{executable} not installed"})
            continue
        try:
            status, public_ip = aws_state(record) if provider == "aws" else gcp_state(record)
            record["status"] = status
            if public_ip:
                record["public_ip"] = public_ip
            record["checked_at"] = now()
            results.append({"provider": provider, "id": record["id"], "result": "updated", "status": status})
            changed = True
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            results.append({"provider": provider, "id": record["id"], "result": "error", "reason": str(exc)})
    if changed:
        save_registry(path, data)
    print(json.dumps({"changed": changed, "path": str(path), "results": results}, indent=2, sort_keys=True))
    return 1 if any(item["result"] == "error" for item in results) else 0


def cmd_copy_ssh(args: argparse.Namespace) -> int:
    tool = clipboard_tool()
    if not tool:
        print(args.command)
        print("Clipboard unavailable; copy the SSH command printed above.", file=sys.stderr)
        return 2
    result = subprocess.run(tool[1], input=args.command, text=True, capture_output=True, check=False, timeout=5)
    if result.returncode:
        print(args.command)
        print(f"{tool[0]} failed; copy the SSH command printed above.", file=sys.stderr)
        return 2
    print(json.dumps({"copied": True, "tool": tool[0]}, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="subcommand", required=True)
    doctor = sub.add_parser("doctor", help="detect cloud CLIs, ssh-keygen, and clipboard support")
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="create or validate the VM registry")
    init.add_argument("--registry")
    init.set_defaults(func=cmd_init)

    key = sub.add_parser("create-key", help="create a dedicated Ed25519 key without a passphrase")
    key.add_argument("--name", required=True)
    key.add_argument("--path")
    key.set_defaults(func=cmd_create_key)

    upsert = sub.add_parser("upsert", help="create or replace one registry record")
    upsert.add_argument("--registry")
    upsert.add_argument("--provider", choices=sorted(VALID_PROVIDERS), required=True)
    for option in ("id", "name", "location", "machine-type", "public-ip", "ssh-user", "private-key", "public-key", "cloud-key-name", "status"):
        upsert.add_argument(f"--{option}", required=True)
    upsert.set_defaults(func=cmd_upsert)

    sync = sub.add_parser("sync", help="query providers and update all registered VM states")
    sync.add_argument("--registry")
    sync.set_defaults(func=cmd_sync)

    copy_ssh = sub.add_parser("copy-ssh", help="copy a direct SSH command or print it as fallback")
    copy_ssh.add_argument("--command", required=True)
    copy_ssh.set_defaults(func=cmd_copy_ssh)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
