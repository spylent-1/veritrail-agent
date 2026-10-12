# RepoPilot development conventions

- All model-requested actions pass through ToolExecutor.
- File edits require prior reading, a current hash and concrete approval.
- Model completion and test verification are separate statuses.
- Keep model credentials in the host environment; do not auto-load repository .env files.
- Keep saved sessions and recovery artifacts outside the target repository.
