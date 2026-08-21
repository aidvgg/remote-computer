#!/usr/bin/env python3
"""Configure one copied GitHub SSH authentication key on a remote VM."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(message)
    return result.stdout.strip()


def fingerprint(path: Path) -> str:
    parts = run(["ssh-keygen", "-lf", str(path)]).split()
    if len(parts) < 2 or not parts[1].startswith("SHA256:"):
        raise RuntimeError(f"could not parse SSH fingerprint for {path}")
    return parts[1]


def expand_remote_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.home() / path


def managed_block(host: str, account: str, private: Path) -> tuple[str, str, str]:
    start = f"# BEGIN remote-computer github-auth {host} {account}"
    end = f"# END remote-computer github-auth {host} {account}"
    block = "\n".join([
        start,
        f"Host {host}",
        f"  HostName {host}",
        "  User git",
        f"  IdentityFile {private}",
        "  IdentitiesOnly yes",
        end,
        "",
    ])
    return start, end, block


def update_ssh_config(config: Path, host: str, account: str, private: Path) -> None:
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    start, end, block = managed_block(host, account, private)
    lines = original.splitlines(keepends=True)
    kept: list[str] = []
    inside_managed = False
    for line in lines:
        stripped = line.strip()
        if stripped == start:
            inside_managed = True
            continue
        if inside_managed:
            if stripped == end:
                inside_managed = False
            continue
        if stripped.startswith("# BEGIN remote-computer github-auth "):
            marker = stripped.split()
            if len(marker) >= 6 and marker[4] == host:
                raise RuntimeError(f"another remote-computer GitHub account already manages {host} in {config}")
            kept.append(line)
            continue
        if stripped.lower().startswith("host ") and host in stripped.split()[1:]:
            raise RuntimeError(f"refusing to override an unmanaged 'Host {host}' block in {config}")
        kept.append(line)
    if inside_managed:
        raise RuntimeError(f"unterminated remote-computer block in {config}")
    remainder = "".join(kept).lstrip("\n")
    updated = block + ("\n" if remainder else "") + remainder
    temporary = config.with_name(f".{config.name}.remote-computer.tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, config)


def configure_git_rewrite(host: str) -> None:
    key = f"url.git@{host}:.insteadOf"
    existing = subprocess.run(
        ["git", "config", "--global", "--get-all", key],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    desired = f"https://{host}/"
    values = existing.stdout.splitlines() if existing.returncode in (0, 1) else []
    if desired not in values:
        run(["git", "config", "--global", "--add", key, desired])


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--host", required=True)
    root.add_argument("--account", required=True)
    root.add_argument("--private-key", required=True)
    root.add_argument("--public-key", required=True)
    root.add_argument("--fingerprint", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if not HOST_RE.fullmatch(args.host):
        raise ValueError("invalid GitHub host")
    if not ACCOUNT_RE.fullmatch(args.account):
        raise ValueError("invalid GitHub account")
    private = expand_remote_path(args.private_key)
    public = expand_remote_path(args.public_key)
    if not private.is_file() or not public.is_file():
        raise FileNotFoundError("copied GitHub key pair is incomplete")
    private_fingerprint = fingerprint(private)
    public_fingerprint = fingerprint(public)
    if private_fingerprint != args.fingerprint or public_fingerprint != args.fingerprint:
        raise ValueError("copied GitHub key pair does not match the expected fingerprint")

    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    os.chmod(private.parent, 0o700)
    os.chmod(private, 0o600)
    os.chmod(public, 0o644)
    config = ssh_dir / "config"
    update_ssh_config(config, args.host, args.account, private)
    configure_git_rewrite(args.host)

    print(json.dumps({
        "account": args.account,
        "host": args.host,
        "fingerprint": args.fingerprint,
        "private_key": str(private),
        "public_key": str(public),
        "ssh_config": str(config),
        "https_rewrite": f"git@{args.host}:",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
