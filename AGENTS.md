# RepoPilot development rules

Read README.md and docs/progress.md before changing behavior. Keep changes in a small,
reviewable task; finish its acceptance checks before starting another capability.

- Keep model SDK handling in deepseek.py and terminal IO in cli.py/ui.py.
- Route model-requested actions through ToolExecutor. Do not add a general shell tool or --yes flag.
- Read only trusted host config. Do not load a target repository's .env or MCP config automatically.
- Preserve existing workspace edits, encoding and line endings. Never use git reset --hard.
- Keep tool call/result messages paired, including denial, cancellation and recovery.
- SQLite is the authoritative journal. Do not separately write a competing trace stream.
- Run pytest, Ruff check/format check, mypy and git diff --check for behavioral changes.
- Label ScriptedFake, real subprocess, real model and remote CI evidence separately.
- Publish only measured metrics. Never report model completion as test success.
- Keep RAG, long-term memory, Skills and multiple agents outside this release.

Offline tests can run without credentials. Live evaluations are deliberate, separately recorded
API calls; their approvals are restricted to disposable evaluation fixtures.
