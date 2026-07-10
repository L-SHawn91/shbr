# Public Release Checklist

Use this before creating the public remote or pushing a release branch.

## Release Shape

- Public OSS pitch: local-first observability for CLI AI agents.
- Default safety promise: no proxy, no SDK, no API key handoff, no prompt text.
- Recommended first public branch: the read-only core plus documented opt-in
  connector boundary.
- Do not make a connector-heavy WIP branch the default until the README clearly
  labels official vs gray connectors.

## Preflight

- `git remote -v` reviewed; public remote intentionally chosen.
- `git status --short` reviewed; no private state, generated local caches, or
  unreviewed WIP included.
- `LICENSE`, `SECURITY.md`, and `CONTRIBUTING.md` present.
- README explains default sources and opt-in connector behavior.
- `python3 -m compileall -q src/shbr` passes.
- `python3 -m shbr --help` works from the checkout.
- macOS app build checked when app changes are included:
  `cd apps/menubar-macos && swift build -c release`.

## Keep Private Unless Explicitly Productized

- private configs and local state;
- screenshots that reveal local paths, account names, private project names, or
  usage details;
- generated assets that include private brand work;
- provider-specific reverse-engineered endpoints before they are labeled `gray`;
- personal SHawn/SHide/SHio routing notes.

## Monetization Track

- OSS core: CLI + safe metadata readers.
- Free app: menu-bar viewer for local individual use.
- Paid/pro: signed macOS app, bundled binary, alerting, export, team dashboards,
  and curated connector packs.
- Enterprise: policy controls, redaction guarantees, fleet install, and audit
  reports.
