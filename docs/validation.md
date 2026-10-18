# Validation and evidence

English · [简体中文](zh-CN/validation.md)

## VeriTrail publication checks — 2026-10-18

Three reliability fixes were committed independently:

| Fix | Commit | Acceptance |
| --- | --- | --- |
| Invalidate verification on every successful write | `8f8529a` | Repeated edit/test/edit flow, failed and denied edits, retesting |
| Bind read evidence to returned complete lines | `a01ff6e` | Byte limits, multibyte text, partial lines, empty files, read grants |
| Include complete Git state with versioned-content checks | `afbb0d0` | Staged/unstaged/untracked, deletion, unborn repos, literal paths, links, binary/large index blobs |

The reliability-fix stage passed **144 local tests in 39.46 seconds**, plus Ruff, formatting, mypy and `git diff --check`. Tests include ScriptedFake model flows and real Windows Git, pytest, Maven and MCP subprocesses. Scripted model completion is not real-model evidence.

The final branded source passed **145 tests in 38.81 seconds**, including an additional mode-only Git change regression. Brand compatibility and package installation are checked separately. The [publication summary](validation/publication.json) binds those checks to source-file fingerprints. Current remote Windows/Ubuntu results are available through [the commit-associated Actions runs](https://github.com/spylent-1/veritrail-agent/actions/workflows/ci.yml); the badge is a live status display, not a historical local-test claim. No new paid model evaluation is included in these publication checks.

## Historical real-model benchmark

Date: 2026-10-12, Asia/Shanghai. Source commit: `095e4027ec7773582feea0035f4bcc427ba3c93f`. Product version: `0.1.0`, then named RepoPilot. Model: `deepseek-flash`, thinking disabled.

Each of 12 fixed Java/Python tasks ran three times in a fresh workspace at a fixed starting commit. The original [benchmark.json](validation/benchmark.json) contains configuration, lockfile and fixture hashes, commits and individual outcomes. A model alias does not freeze server-side weights.

| Category | Strict passes | Runs |
| --- | ---: | ---: |
| Source localization | 15 | 18 |
| Code repair with independent acceptance | 18 | 18 |

This met the planned localization threshold of 15/18 and repair threshold of 12/18. EJ1 and EP1 each passed three consecutive attempts, exercising Maven/JUnit and pytest from original failure to accepted repair. There were 153 model responses and 223 tool calls. Returned usage totaled 402485 input and 32033 output tokens; summed run duration was 332.8 seconds. These are fixed-fixture observations, not throughput measurements or general capability rankings.

Localization failures were LJ3 attempt 3, LJ4 attempt 3 and LP1 attempt 1. Answers found the intended code but failed strict evidence-ID formatting or method-declaration-line coverage. They remain failures. Graders check cited paths, lines and current hashes; they do not prove every natural-language inference correct.

Raw SQLite, trace, workspace, report and independent-grader artifacts remain in the local ignored `.eval-runs/live-v0.1.0/` directory. The public repository includes selected summaries, not the private raw artifacts. Historical summaries retain original names, paths and hashes and are not rewritten as current-version results.

## Historical MCP and self-maintenance evidence

- A real stdio example server was called through DeepSeek in 2 model turns and 1 tool call. The [first MCP report](validation/mcp-live.json) contained an invented E1 citation that the runtime flagged as unknown.
- A self-maintenance task used an isolated source copy to fix directory diffs failing on binary content. The [initial report](validation/self-maintenance-initial.json) records 7 model turns, 8 tool calls and passing root-level tests.
- After subdirectory regressions were added, the same session continued for 5 model turns and 6 tool calls. Three regressions passed with no final citation warning; see [the follow-up report](validation/self-maintenance.json).
- The local fix was reviewed and integrated. The 36-run benchmark remains tied to the earlier source commit; later fixes were separately tested rather than presented as a benchmark rerun.

## Historical delivery boundary

The original delivery ran real Windows subprocess, Maven, pytest, MCP, filesystem, workspace-lock, recovery and rollback checks. Ruff, formatting and Windows/Linux-targeted mypy passed. Remote CI and Ubuntu runtime had not yet been verified at that historical delivery.

That delivery's final gate was 113 tests. Wheel and sdist builds were installed into an independent environment, imports came from site-packages, six then-existing commands were visible, and doctor/session queries worked without an API key. The original [installation report](validation/install-smoke.json) remains unchanged. A later continuous-chat change raised the local total to 125; see [development history (Chinese)](progress.md).

No large production-repository model evaluation, production sandbox, RAG or multi-agent capability is claimed. Runtime results do not establish that a reader has completed the source-study exercises.

## Reproducing evaluations

```sh
# Check independent graders without model calls
python -m uv run python -m evals.run --check-graders
# Optional paid real-model benchmark: 12 tasks x 3 repeats
python -m uv run python -m evals.run --repeats 3 --output .eval-runs/live
```

Evaluation approval is limited to disposable fixtures and fixed test profiles. Maven can download pinned fixture dependencies on the first run. Partial runs do not count as a complete release evaluation. Keep raw artifacts in the ignored output directory.
