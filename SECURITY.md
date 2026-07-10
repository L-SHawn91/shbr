# Security Policy

`shbr` is designed as a local-first, read-only observer for agent metadata. The
default sources should not call the network, collect prompt/completion content,
or require users to hand over API keys.

## Supported Reports

Please report:

- Any path where prompt or completion content is emitted unexpectedly.
- Any default source that mutates a source file, database, credential, or remote
  account.
- Any accidental logging of access tokens, refresh tokens, cookies, API keys, or
  full credential payloads.
- Any connector that runs without explicit opt-in.

Do not include real credentials, private prompts, or private session transcripts
in a public issue. Use a redacted minimal repro.

## Connector Boundary

Live quota connectors are optional. They may reuse credentials that a provider's
own CLI or app already placed on the machine, but they must:

- be off by default;
- fail silent on auth/network errors;
- avoid writing refreshed credentials back to disk;
- return metadata only;
- clearly label undocumented, internal, or reverse-engineered endpoints as
  `experimental` and declare every remote hostname.

Connector quota reads and OAuth refreshes may use GET or POST. They must not
change provider account content or persist refreshed credentials. Run
`shbr doctor` for a redaction-safe view of enabled connectors and hosts.

## Disclosure

Until GitHub Security Advisories are enabled for the public repository, open a
minimal public issue that says a private security report is needed, without
including the sensitive details.
