# Documentation Maintenance

This repository uses docs-as-code for user, developer, and GitHub Wiki
documentation. Documentation must describe reachable production behavior; old
implementation plans must be marked as completed records rather than current
architecture.

## Source of truth

- Repository/agent contracts live in `README.md`, `CONTRIBUTING.md`,
  `PRIVACY.md`, and `AGENTS.md`.
- Current architecture and user guides live in `docs/wiki/`.
- UI behavior contracts and human validation live in `docs/UI_*`.
- JSON evaluation contracts live in `docs/schemas/`.
- Every Markdown file in `docs/wiki/` is published; `Home.md` is the wiki home.
- `Project-README.md` is generated from `/README.md`; component documentation is
  maintained directly in `docs/wiki/`.

## Automated publishing

The workflow `.github/workflows/publish-wiki.yml` publishes docs to the repository wiki:

- Triggered on push to `main` when docs or README files change
- Can also be started manually via `workflow_dispatch`

## Local regeneration and checks

You can run the publisher script manually if you have push access:

```bash
bash scripts/publish-wiki.sh
```

Regenerate README-derived wiki pages after changing the root README:

```bash
bash scripts/build-wiki-pages.sh
```

Before handoff, inspect repository links and stale terminology, run the plug-in
validator, and run code validation appropriate to any behavior documented in
the same change. Publishing requires:

- `GITHUB_REPOSITORY` (for example `RareLight/StyleAI`)
- `GITHUB_TOKEN` with write access to the wiki
