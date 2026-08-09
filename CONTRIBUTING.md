# Contributing to StyleAI

StyleAI is an AGPL-3.0 local-first Lightroom Classic plug-in. Read
[`AGENTS.md`](AGENTS.md) and the [architecture guide](docs/wiki/Architecture.md)
before changing behavior.

## Development setup

Requirements: Git, `uv`, Python 3.12+, and Lightroom Classic for integration
testing.

```sh
git clone https://github.com/YOUR_USERNAME/StyleAI.git
cd StyleAI
bash scripts/setup-local-uv-env.sh
```

The backend environment is locked by `server/pyproject.toml` and
`server/uv.lock`. Use `uv add`/`uv add --dev`; never add `requirements.txt`.

Add `plugin/StyleAI.lrdevplugin` through Plug-in Manager for source testing, or
build an isolated developer package:

```sh
python scripts/package_lrc_plugin.py developer
```

The generated `build/StyleAI-dev.lrdevplugin` contains developer-only Help menu
commands. The checked-in plug-in remains a release build.

## Implementation rules

- Keep the service loopback-only and catalog-local. Do not add cloud providers,
  remote hosts, API keys, telemetry, or cross-catalog routing.
- Put HTTP parsing in `server/src/routes`, business logic in `services`, and
  Ollama/LM Studio integration in `providers`.
- Surface user errors in Lightroom through `ErrorHandler`; log Python
  exceptions through the configured logger with `exc_info=True`.
- Run long Lua work asynchronously with `LrTasks.pcall`; batch catalog writes
  and never yield inside a write transaction. Shutdown uses the documented
  native-`pcall` no-I/O exception.
- Preserve durable operation jobs, resource admission, catalog ownership,
  atomic policy activation, immutable edit history, and the distinction between
  visual-index and training collections.
- Learned editing predicts absolute targets and abstains on ambiguity. Do not
  introduce genre taxonomies or keyword-based membership gates.
- Update tests when behavior changes and update user/architecture documentation
  in the same pull request.

## Validation

Run the relevant focused tests while iterating, then the complete checks:

```sh
bash server/scripts/lint_format.sh
cd server && uv run pytest test/
cd ..
python scripts/validate_lrc_plugin.py
```

Run `python scripts/package_lrc_plugin.py developer`, reload that package in a
disposable catalog, and use **Developer: Run Automated Tests...** for changes
that cross the Lua/backend boundary. Follow `docs/UI_HUMAN_TEST_MATRIX.md` for
UI or catalog-changing work.

Policy/recommendation changes must also run:

```sh
cd server
uv run python scripts/evaluate_editing_policies.py
uv run python scripts/benchmark_policy_scaling.py
```

Use the catalog evaluators only against a local test catalog; they are
evaluation-only and do not activate models.

## Documentation and localization

- Source wiki pages live in `docs/wiki/`; `Project-README.md` is generated from
  the root README by `bash scripts/build-wiki-pages.sh`.
- Wrap all user-visible Lua strings in `LOC()`.
- Keep `TranslatedStrings_en.txt`, `TranslatedStrings_ca.txt`,
  `TranslatedStrings_de.txt`, `TranslatedStrings_es.txt`, and
  `TranslatedStrings_fr.txt` synchronized. Run `python sync_translations.py`
  and the plug-in validator, then inspect any locale the sync helper does not
  update mechanically.

## Pull requests

Keep changes scoped, explain data or compatibility effects, list exact checks
run, and identify Lightroom-only validation that remains. Never include
catalog databases, photos, model caches, logs, diagnostic captures, or secrets.
