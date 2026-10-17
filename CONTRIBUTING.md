# Contributing

Thanks for improving Claude Flex. Keep the everyday experience small:
one screen, keyboard navigation and progressive disclosure of advanced settings.

## Local setup

```bash
git clone https://github.com/matrixdurden/claude-flex.git
cd claude-flex
uv sync
uv run claude-flex
```

For isolated experiments set `CLAUDE_FLEX_HOME` to a private temporary directory.
Never add real account files, API keys, logs or generated Desktop profiles to Git.

## Checks

```bash
uv run python -m unittest discover -s tests -v
uvx ruff check .
uv build
```

Optional gateway integration:

```bash
CLAUDE_FLEX_TEST_BACKEND=/path/to/cli-proxy-api uv run python -m unittest tests.test_integration -v
```

Tests use synthetic credentials and local upstreams. Do not add tests that log
in to real providers or make chargeable inference calls in CI. Add meaningful
coverage for account ownership, routing changes and TUI interaction flows.

README screenshot data must be synthetic. To regenerate:

```bash
uv run python scripts/render_demo.py
```

Open a focused pull request explaining the user-facing behavior and verification.
By contributing, you agree that your contribution is licensed under MIT.
