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
  ]
}
```

For GCP, `id` is the instance name used by `gcloud compute instances describe` and `location` is its zone. VM identity is the tuple `(provider, id)`.

## Integrity and recovery

- Keep mode `0600`; the helper writes atomically through a temporary file and `os.replace`.
- Never overwrite malformed JSON or an unsupported schema. Report the error and ask before recovery.
- Upsert only from actual provider output. Preserve `created_at` when updating an existing record.
- On a successful provider status query, update `status`, `public_ip` when present, and `checked_at`.
- On CLI absence, authentication failure, timeout, permission error, malformed output, or ambiguous lookup, preserve prior VM fields and report the skipped/failed check.
- AWS can unambiguously return an empty successful instance result, which the helper records as `missing`. GCP “not found” currently arrives as a command error and is deliberately preserved for human review.
- Do not remove records merely because a VM is stopped, terminated, suspended, or missing. Historical/removal policy requires an explicit user request.

Run `python3 scripts/vm_bookkeeper.py sync` before the final handoff whenever the registry predated the current session. A nonzero exit means at least one provider check failed; include those failures in the handoff rather than hiding them.
