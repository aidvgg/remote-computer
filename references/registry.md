# VM registry

`~/.vms.json` is local bookkeeping for live remote development VMs. It is not a credential store and must never contain private-key contents, cloud secrets, tokens, or copied credential configuration.

## Schema

The helper owns schema version 1:

```json
{
  "version": 1,
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00",
  "vms": [
    {
      "provider": "aws",
      "id": "i-0123456789abcdef0",
      "name": "dev-box",
      "location": "us-east-1",
      "machine_type": "t3.large",
      "public_ip": "203.0.113.10",
      "ssh_user": "ubuntu",
      "key_pair": {
        "private_path": "/home/user/.ssh/remote-computer/dev-box",
        "public_path": "/home/user/.ssh/remote-computer/dev-box.pub",
        "cloud_name": "remote-computer-dev-box"
      },
      "status": "running",
      "created_at": "2026-01-01T00:00:00+00:00",
      "checked_at": "2026-01-01T00:00:00+00:00"
    }
  ],
  "github_auth": [
    {
      "host": "github.com",
      "account": "octocat",
      "key_pair": {
        "private_path": "/home/user/.ssh/remote-computer/github/github.com-octocat",
        "public_path": "/home/user/.ssh/remote-computer/github/github.com-octocat.pub"
      },
      "source": "generated",
      "fingerprint": "SHA256:example-fingerprint",
      "title": "remote-computer shared octocat",
      "created_at": "2026-01-01T00:00:00+00:00",
      "installed_on": [
        {
          "provider": "aws",
          "id": "i-0123456789abcdef0",
          "remote_private_path": "~/.ssh/remote-computer/github/github.com-octocat-example",
          "remote_public_path": "~/.ssh/remote-computer/github/github.com-octocat-example.pub",
          "configured_at": "2026-01-01T00:00:00+00:00"
        }
      ]
    }
  ]
}
```

For GCP, `id` is the instance name used by `gcloud compute instances describe` and `location` is its zone. VM identity is the tuple `(provider, id)`.

`github_auth` is optional and additive in schema version 1. Registries created before GitHub porting omit it and remain valid. Each host/account pair identifies one shared GitHub authentication key that may be installed on multiple VMs. This top-level collection is deliberately separate from every VM's `key_pair`, which remains the cloud-login key and keeps its original shape.

The registry stores only key paths, public fingerprints, labels, sources, and installation metadata. It never stores private/public key contents, GitHub tokens, GPG secret packets, passphrases, or credential-helper data.

## Integrity and recovery

- Keep mode `0600`; the helper writes atomically through a temporary file and `os.replace`.
- Never overwrite malformed JSON or an unsupported schema. Report the error and ask before recovery.
- Upsert only from actual provider output. Preserve `created_at` when updating an existing record.
- Refuse silent GitHub key rotation. A different fingerprint for an existing host/account requires an explicit cleanup/rotation workflow.
- Add an `installed_on` entry only after the remote helper verifies copied key fingerprints and SSH configuration. A failed copy or verification must not claim success.
- On a successful provider status query, update `status`, `public_ip` when present, and `checked_at`.
- On CLI absence, authentication failure, timeout, permission error, malformed output, or ambiguous lookup, preserve prior VM fields and report the skipped/failed check.
- AWS can unambiguously return an empty successful instance result, which the helper records as `missing`. GCP “not found” currently arrives as a command error and is deliberately preserved for human review.
- Do not remove records merely because a VM is stopped, terminated, suspended, or missing. Historical/removal policy requires an explicit user request.

Run `python3 scripts/vm_bookkeeper.py sync` before the final handoff whenever the registry predated the current session. A nonzero exit means at least one provider check failed; include those failures in the handoff rather than hiding them.
