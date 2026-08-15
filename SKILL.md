---
name: remote-computer
description: Configure and provision a remote development VM on AWS EC2 or Google Cloud Compute Engine, verify cloud CLI authentication and project/account context, choose a predefined machine tier including Google Cloud Free Tier where eligible, create dedicated SSH keys, maintain the ~/.vms.json live-VM registry, reconcile VM status, and prepare a direct SSH command. Use when a user asks to create, deploy, configure, resume, inspect, or connect to a remote cloud development environment or VM on AWS or GCP.
---

# Remote Computer

Provision one auditable development VM at a time. Resolve bundled `scripts/` and `references/` paths relative to the directory containing this `SKILL.md`, not the user's current project. Treat cloud creation, firewall changes, and billable resources as consequential actions: inspect first, show the exact deployment summary, and obtain explicit confirmation immediately before creating resources.

## Non-negotiable rules

- Never expose credentials, private key contents, access tokens, or cloud credential files.
- Never assume that a tier is free. Show provider, region/zone, machine type, disk, image, network exposure, and likely billing status before confirmation.
- Restrict SSH ingress to the user's confirmed public IPv4 `/32` or IPv6 `/128`; do not open port 22 to the world unless the user explicitly requests it after a warning.
- Create a dedicated unencrypted SSH key only when the user did not explicitly provide a key. Store private keys under `~/.ssh/remote-computer/` with mode `0600`; registry records contain paths, never key material.
- Do not delete instances, keys, disks, firewall rules, or registry entries unless the user explicitly requests cleanup and confirms the resolved targets.
- Do not mark a VM stopped or missing merely because a CLI, network, permission, or parsing error occurred. Preserve its previous status and report the failed check.
- Use `scripts/vm_bookkeeper.py` for keys, registry writes, status reconciliation, and clipboard handling. Do not hand-edit `~/.vms.json` when the helper can perform the operation.

## Workflow

### 1. Discover local tools

Run:

```bash
python3 scripts/vm_bookkeeper.py doctor
```

Before changing the registry, note whether `~/.vms.json` already exists for the end-of-run reconciliation rule. Then locate, create, or validate it:

```bash
python3 scripts/vm_bookkeeper.py init
```

Identify `aws` and `gcloud` independently. Tell the user which are installed, then always ask whether to deploy to AWS or Google Cloud. If the selected CLI is absent, pause provisioning and offer its official installation instructions; do not silently switch providers.

### 2. Ask for a tier

Offer these presets and allow a custom machine type if the user asks:

| Tier | Intended use | AWS | Google Cloud |
| --- | --- | --- | --- |
| Starter | Light coding and small services | `t3.medium` (2 vCPU, 4 GiB) | `e2-medium` (2 shared-core vCPU, 4 GiB) |
| Standard | General development | `t3.large` (2 vCPU, 8 GiB) | `e2-standard-2` (2 vCPU, 8 GiB) |
| Performance | Builds and multi-service work | `t3.xlarge` (4 vCPU, 16 GiB) | `e2-standard-4` (4 vCPU, 16 GiB) |
| Heavy | Large builds and data workloads | `t3.2xlarge` (8 vCPU, 32 GiB) | `e2-standard-8` (8 vCPU, 32 GiB) |
| Google Cloud Free Tier | Small, low-throughput work | Not applicable | One eligible non-preemptible `e2-micro` allowance in `us-west1`, `us-central1`, or `us-east1` |

Describe Google Cloud's option as a Free Tier allowance, not “guaranteed free.” Before selecting it, read `references/gcp.md` and state its region, disk, egress, billing-account, and aggregate monthly usage constraints. Existing usage can consume the allowance.

Ask for the provider first, then tier. Afterward resolve a VM name, AWS region or GCP zone, OS image, disk size/type, network, and SSH source CIDR. Default to Ubuntu 24.04 LTS, a provider-generated ephemeral public IP, user `ubuntu`, and a 30 GiB encrypted boot disk when compatible with the selected allowance.

### 3. Verify the selected provider

For AWS, read `references/aws.md`. Perform at least:

1. `aws --version` and `aws sts get-caller-identity`.
2. Resolve the active profile and region explicitly, then verify EC2 access and inspect the selected VPC/subnet.

For Google Cloud, read `references/gcp.md`. Perform at least:

1. `gcloud version` and `gcloud auth list --filter=status:ACTIVE`.
2. Resolve and confirm the project and zone explicitly, verify project access, and verify the Compute Engine API/network context.

Show the resolved account identity plus region/project without exposing secrets. If identity, billing context, permissions, or target location is ambiguous, ask the user to resolve it before provisioning.

### 4. Prepare SSH access

If the user supplies a key, validate that the private path and matching `.pub` file exist; never copy or print the private key. Otherwise run:

```bash
python3 scripts/vm_bookkeeper.py create-key --name <vm-name>
```

The helper uses Ed25519 and an empty passphrase. Import only the public key into AWS, or attach only the public key to GCP instance metadata as described by the provider reference. Use a unique provider-side key or firewall name derived from the VM name; detect collisions before creation.

### 5. Confirm and provision

Present one compact summary containing provider account/project, location, machine type, image, boot disk, network, SSH CIDR, public-key path, all resources to be created, and the fact that charges may apply. Ask: “Create these cloud resources now?” Continue only after an explicit yes.

Follow the selected provider reference. Capture provider-generated IDs from command output, wait for the instance to reach its running state, and verify that both the provider status and public IP are available. Do not claim success based only on a create command's exit code.

### 6. Register the VM

The registry should already be initialized from step 1. After successful provisioning, upsert the VM using provider-native identity and the actual returned values:

```bash
python3 scripts/vm_bookkeeper.py upsert \
  --provider <aws|gcp> --id <instance-id-or-name> --name <name> \
  --location <aws-region-or-gcp-zone> --machine-type <type> \
  --public-ip <ip> --ssh-user <user> --private-key <path> \
  --public-key <path.pub> --cloud-key-name <provider-key-name> \
  --status running
```

The registry schema and recovery rules are in `references/registry.md`. Record paths and metadata only. If provisioning partially fails, report created resource IDs and add a record only when an instance actually exists.

### 7. Reconcile live status before every handoff

If `~/.vms.json` existed before this session, run the following near the end of the run—even when provisioning was cancelled or failed:

```bash
python3 scripts/vm_bookkeeper.py sync
```

This checks every registered AWS/GCP VM for which the corresponding CLI is available and atomically updates status, IP, and `checked_at`. Report skipped or failed checks. If the file was created during this session, synchronization is optional, but ensure any newly created VM record already contains its verified live state.

### 8. Prepare direct SSH access

Build a direct command from the registry values:

```bash
ssh -o IdentitiesOnly=yes -i <private-key-path> <ssh-user>@<public-ip>
```

Attempt clipboard copy with:

```bash
python3 scripts/vm_bookkeeper.py copy-ssh --command '<ssh command>'
```

The helper quickly discovers `pbcopy` on macOS or `wl-copy`, `xclip`, or `xsel` on Linux. If copying fails, print the command plainly. End by telling the user to open a new terminal, paste the command, and connect. Mention that first connection host-key verification is expected; do not disable it globally.
