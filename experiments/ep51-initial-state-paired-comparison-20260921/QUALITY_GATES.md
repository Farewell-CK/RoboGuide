# Quality gates

- Targeted Habitat adapter tests: PASS.
- `uv run ruff format --check integrations/habitat-local-eaios`: PASS, 20 files.
- `uv run ruff check integrations/habitat-local-eaios`: PASS.
- `uv run mypy --strict integrations/habitat-local-eaios/habitat_local_eaios integrations/habitat-local-eaios/tests`: PASS, 19 source files.
- `uv run python tools/quality/check_python_function_docs.py integrations/habitat-local-eaios`: PASS.
- `git diff --check`: PASS.
- `cargo build --bin integration-server --bin roboguide-node`: PASS.
- Pre-merge full repository `uv run pytest -q`: PASS.
- Local and remote main: `a24feeba4b84336d870a235e56c46fb91b2d6d67`.
