# RepoPilot

面向个人可信 Java/Python 仓库的 Coding Agent CLI。Python 3.12，自研顺序工具循环，
核心目标是可检查的读取、审批、修改、测试和恢复。不是恶意代码沙箱。

## 开发

```powershell
python -m pip install --user uv
python -m uv sync --locked
python -m uv run repopilot --help
python -m uv run repopilot doctor
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy
```

uv 在 PATH 时可以直接使用 `uv`。API Key 仅从宿主环境变量 `DEEPSEEK_API_KEY`
读取，不加载目标仓库的 `.env`。宿主配置位于 `~/.repopilot/config.toml`。

实现进度和验证边界见 [开发记录](docs/progress.md)。
