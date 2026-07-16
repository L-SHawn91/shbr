# Contributing

Thanks for improving `shbr`.

## Project Contract

`shbr` should stay boring in the ways that matter:

- local-first by default;
- read-only against source files, source databases, credentials, and provider
  accounts;
- metadata-only output;
- no prompt or completion content;
- no dependency-heavy core path;
- opt-in network connectors only.

## Development

```bash
python3 -m compileall -q src/shbr
python3 -m shbr --help
python3 -m shbr snapshot --json
```

For the macOS menu-bar app:

```bash
cd apps/menubar-macos
swift build -c release
```

## Connector Rules

New live quota connectors must be off by default, declare their remote hosts,
and be registered through the connector registry. A connector must declare
whether it is:

- `documented`: backed by a publicly documented provider API;
- `experimental`: undocumented, internal, or reverse-engineered and shown with
  an explicit warning.

First-party ownership of a hostname is not documentation. If the provider has
no stable usage API, prefer documenting the gap over shipping a speculative
connector.

## Pull Requests

Keep changes small and explain the source boundary touched by the change:

- local file metadata;
- local SQLite metadata;
- OS keychain presence check;
- provider network quota read;
- UI rendering only.

Never commit private configs, generated local state, `.DS_Store`, credentials,
or source transcripts.
