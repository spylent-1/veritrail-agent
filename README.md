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

## 使用

推荐使用连续对话：如果已激活项目虚拟环境，执行以下命令启动一次，此后直接输入任务。

```powershell
repopilot chat
# 允许提出编辑和测试请求，具体操作仍逐次审批：
repopilot chat --workspace D:\YourJavaRepo --mode edit
```

未激活虚拟环境时，可以使用 `python -m uv run repopilot chat`。
连续提问共用一个 SQLite 会话，每条任务独立记录运行报告；默认是只读模式。
输入 `/help` 查看命令，`/new` 清空当前对话上下文并开始新会话，`/exit` 退出。
`/new` 保留当前工作区和模式；空行不调用模型，输入中断或文件结束会退出。

退出后，用 `repopilot sessions` 查看会话，再执行 `repopilot chat --session <会话ID>` 继续。
继续时沿用保存的工作区和上次模式，旧审批失效；MCP 仍需额外显式传入 `--mcp docs`。
编辑已有文件时仍需在本次任务重新读取，不复用上一条任务的读取授权。
Ctrl+C 在任务执行时会取消并结束对话，已保存的状态下次核对后继续，不自动重放。
交互模式要求真实终端；单次任务或脚本调用仍使用 `run`：

```powershell
python -m uv run repopilot run "追踪优惠券领取调用链，引用源码行号" --workspace D:\YourJavaRepo
python -m uv run repopilot run "定位 bug，审批后局部修复并运行 java-unit" --workspace D:\YourJavaRepo --mode edit
python -m uv run repopilot sessions
python -m uv run repopilot resume <session-id>
python -m uv run repopilot trace <run-id> --jsonl
python -m uv run repopilot rollback <run-id>
```

先运行 doctor。测试命令必须配置在宿主 config.toml 中，模型只能选择配置名；示例见
[config.example.toml](docs/config.example.toml)，修改为自己的绝对解释器/Maven 路径。
输入 edit 不等于自动批准，具体差异及测试命令仍需确认；管道或非交互模式拒绝副作用。

如需 MCP，在宿主配置增加 Server，并使用 `--mcp docs` 显式选择：

```toml
[mcp.docs]
executable = 'D:\PythonProjection\AgentCli\.venv\Scripts\python.exe'
args = ['-m', 'repopilot.mcp_sample', '--root', 'D:\PythonProjection\AgentCli\docs']
tools = ['get_project_conventions']
timeout = 20
```

Server 启动和工具调用分别审批。不会自动执行目标仓库里的配置，也不会自动安装外部 Server。

## 评测与交付

```powershell
python -m uv run python -m evals.run --check-graders
python -m uv run python -m evals.run --repeats 3 --output .eval-runs/live
python -m uv build
```

评测是 12 个固定 Java/Python 样例各重复 3 次。运行会调用真实模型；完整结果、SQLite、Trace
及工作区保存在指定目录。独立评分器位于模型工作区之外。部分运行标记 partial，不算发行验收。
首次 Maven 测试可能从 Maven 仓库下载已固定的依赖；“无模型测试”不等于所有环境都完全离线。

实现进度见 [开发记录](docs/progress.md)，验证结果见 [验收记录](docs/validation.md)，
调用链见 [架构](docs/architecture.md)，学习与面试练习见 [源码路线](docs/learning.md)，
实际能力边界见 [信任边界](docs/security.md)。
