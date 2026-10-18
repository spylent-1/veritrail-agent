# Trust and security boundaries

English · [简体中文](zh-CN/security.md)

VeriTrail is intended for repositories you trust. Edits, tests and external tools run with the host user's permissions. A command allow-list is not an OS sandbox: pytest code, Maven plugins and MCP servers can execute their own logic.

The model cannot alter host policy. Read mode does not register editing or testing tools. Edit mode asks for each concrete operation; MCP requires separate explicit selection. Non-interactive terminals refuse side effects. There is no approve-all flag. Evaluation-only approval handlers authorize specified files and fixed test profiles in disposable fixtures; they are not included in the installed CLI.

Built-in file tools share Workspace checks: relative paths, sensitive-file filtering, rejection of symlinks/junctions/hard links, size limits and UTF-8 validation. Reads and searches exclude secrets, internal state and generated directories. Git diffs also check historical and index content. Ordinary ignore files are not security rules. The default host directory, `~/.repopilot`, contains private source snapshots and must not be committed.

The API key is read only from the host's `DEEPSEEK_API_KEY` environment variable. Known key values and terminal control sequences are removed from presentation and tool logs. Recovery snapshots preserve original file bytes without redaction and must be treated as private source material. Filtering is not a general secret detector and cannot identify every hard-coded credential. Allowed source content is sent to the configured model service; ensure your repository can be processed this way.

Tests and MCP subprocesses use a reduced environment without the model key. Tests select named host profiles; model-supplied commands and extra arguments are rejected. Windows Maven uses an installed `mvn.cmd`, rejects shell metacharacters, and still relies on the batch launcher internally. Timeouts and cancellation clean up ordinary subprocess trees. Malicious processes and network access are not isolated.

Approvals bind to concrete diffs or argv/cwd parameters. Writes and rollback recheck file hashes before execution. Another malicious local process can still race filesystem replacement. Untrusted databases, build plugins and servers receive no sandbox guarantees.

Verification describes the observed test result. Every successful agent write invalidates earlier verification, but changes made outside the agent require inspection and fresh tests. A completed model answer alone is never test success.
