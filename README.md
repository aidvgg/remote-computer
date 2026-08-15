# remote-computer

A Codex/Claude agent skill for provisioning auditable remote development VMs on AWS EC2 or Google Cloud Compute Engine. It discovers installed cloud CLIs, verifies account/project context, offers practical machine presets, creates dedicated SSH access, tracks live instances in `~/.vms.json`, reconciles provider status, and places a direct SSH command on the clipboard.

## Install or update

```sh
curl -fsSL https://raw.githubusercontent.com/paoloanzn/remote-computer/main/install-skill.sh | sh
```

The installer auto-detects Codex or Claude. Override with `AGENT=codex`, `AGENT=claude`, or `SKILLS_DIR=/custom/path`.

## Use

Ask the agent to use `$remote-computer`, for example: “Use `$remote-computer` to create an AWS development VM” or “Provision a small GCP VM and prepare SSH access.” The skill guides the agent through provider and tier selection, identity/network checks, an explicit pre-creation confirmation, provisioning, registry update, live-state verification, and connection handoff.

| Tier | AWS | Google Cloud |
| --- | --- | --- |
| Starter | `t3.medium` · 2 vCPU · 4 GiB | `e2-medium` · 2 shared vCPU · 4 GiB |
| Standard | `t3.large` · 2 vCPU · 8 GiB | `e2-standard-2` · 2 vCPU · 8 GiB |
| Performance | `t3.xlarge` · 4 vCPU · 16 GiB | `e2-standard-4` · 4 vCPU · 16 GiB |
| Heavy | `t3.2xlarge` · 8 vCPU · 32 GiB | `e2-standard-8` · 8 vCPU · 32 GiB |
| Free Tier allowance | — | eligible `e2-micro` usage in selected US regions; limits apply |

Requirements: Python 3.10+, `ssh-keygen`, and at least one configured provider CLI (`aws` or `gcloud`). Clipboard handoff uses `pbcopy` on macOS or `wl-copy`, `xclip`, or `xsel` on Linux, with a printed-command fallback.

## Safety and state

The skill never stores private-key material or cloud credentials in its registry. New dedicated Ed25519 keys are created without a passphrase under `~/.ssh/remote-computer/`; `~/.vms.json` stores only provider IDs, location, status, IP, SSH user, and key paths, is written atomically with mode `0600`, and is reconciled against provider APIs before handoff. SSH ingress defaults to the user's confirmed `/32` or `/128`; cloud resources are created only after the agent displays the resolved account, location, machine, disk, network exposure, keys, and potential billing impact and receives explicit confirmation. “Free Tier” is an allowance, not a guarantee of zero cost.

Provider procedures and official sources live in [`references/aws.md`](references/aws.md), [`references/gcp.md`](references/gcp.md), and [`references/registry.md`](references/registry.md). The deterministic helper is [`scripts/vm_bookkeeper.py`](scripts/vm_bookkeeper.py):

```sh
python3 scripts/vm_bookkeeper.py doctor
python3 scripts/vm_bookkeeper.py init
python3 scripts/vm_bookkeeper.py sync
```

## Develop

```sh
python3 -m pip install PyYAML
npx --yes pyright@1.1.413 --level error
python3 -m unittest discover -s tests -v
python3 scripts/quick_validate.py .
sh -n install-skill.sh
```

CI runs strict Pyright, unit/integration tests, skill validation, and shell syntax checks on pushes and pull requests.
