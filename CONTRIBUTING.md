# Contributing to VeriTrail

Start with the [README](README.md), [development rules](AGENTS.md) and
[development history](docs/progress.md). Keep each pull request focused on one
problem and explain how you checked the result.

## Development setup

Use Python 3.12, Git and ripgrep (`rg`) on PATH. The Java fixtures also require
JDK 17 and Maven. From the repository root, install the locked dependencies:

```sh
python -m pip install --user uv
python -m uv sync --locked
```

## Checks before a pull request

The [CI workflow](.github/workflows/ci.yml) runs on Windows and Ubuntu:

```sh
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy
python -m uv build
git diff --check
```

Offline tests do not require model credentials. Maven may download fixture
dependencies on the first run. Real-model evaluations are separate, deliberate
API calls; follow the [validation guide](docs/validation.md) before running them.
For documentation-only changes, inspect the full diff and check Markdown links;
state which checks you actually ran.

## Pull request description

Describe the problem, the change and the validation results. Link an existing
issue when relevant. Include a regression test for a behavioral fix, and keep
unrelated changes out of the pull request.

Follow the trust boundaries in [AGENTS.md](AGENTS.md): model-requested actions
must go through ToolExecutor, approvals must remain concrete, and target
repositories must not supply trusted host configuration. Keep test, real-model
and remote CI evidence separate. Never include API keys, private configuration
or session data in a contribution.
