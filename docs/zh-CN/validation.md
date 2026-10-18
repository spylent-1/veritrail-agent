# VeriTrail 验收记录

[English](../validation.md) · 简体中文

## 2026-10-18 发布检查

三个可信度问题已分别修复并提交：重复写入后验证失效（`8f8529a`）、读取截断与证据范围（`a01ff6e`）、
完整 Git 状态与版本内容过滤（`afbb0d0`）。修复阶段本地 144 项测试通过（39.46 秒），
Ruff、格式检查、mypy 和 git diff --check 通过。测试包含 ScriptedFake 模型流程与真实 Windows
Git、pytest、Maven 和 MCP 子进程；未新增付费模型评测。

最终更名源码含仅权限变化的 Git 回归，共 145 tests 通过（38.81 秒）。
英文优先的双语包装、更名兼容和安装检查单独验收，最终安装及源码指纹见
[发布验收摘要](../validation/publication.json)。远端 Windows/Ubuntu 状态以
[对应提交的 Actions](https://github.com/spylent-1/veritrail-agent/actions/workflows/ci.yml) 为准。

## 复现评测

```powershell
# 独立评分器检查，不调用模型
python -m uv run python -m evals.run --check-graders
# 可选：36 次真实模型评测，会产生 API 费用
python -m uv run python -m evals.run --repeats 3 --output .eval-runs/live
```

API 评测审批仅限一次性夹具。原始工件保存在忽略的 `.eval-runs/`；下方为历史版本的原始验收说明，
其中旧项目名、旧命令和当时未验证的远端状态均保留其原始时间含义。

---

# v0.1.0 验收记录

验证日期：2026-10-12（Asia/Shanghai）。计划版本 V2.0，产品版本 v0.1.0。

## 真实模型固定样本

来源版本：`095e4027ec7773582feea0035f4bcc427ba3c93f`。默认模型 `deepseek-flash`，thinking disabled。
每个任务使用全新工作区和固定起始提交；完整配置、锁文件哈希、样本哈希、提交与逐项数据见
[benchmark.json](../validation/benchmark.json)。模型别名不能冻结服务端权重，数据只代表本次运行。

| 类型 | 严格通过 | 样本数 |
|---|---:|---:|
| Java/Python 代码定位 | 15 | 18 |
| Java/Python 代码修复＋独立验收 | 18 | 18 |

满足计划的定位 ≥15/18、修改 ≥12/18 门槛。EJ1、EP1 各连续三次通过，分别演示 Maven/JUnit 与
pytest 的“原始失败→局部修改→测试通过”。共 153 次模型响应、223 次工具调用；返回 usage
累计输入 402485、输出 32033 tokens；逐次耗时相加 332.8 秒。不是并发吞吐测试或通用能力排名。

三个定位失败：LJ3 第 3 次、LJ4 第 3 次、LP1 第 1 次。实际回答定位了目标源码，但没有满足
严格的 `[E编号]` 引用格式或方法声明行覆盖规则；未把这些结果改记为通过。定位评分检查路径、
方法名、引用行号与当前哈希，并不能自动证明自然语言分析的所有推断正确。

原始 SQLite、Trace、工作区、报告与独立评分输出保存在 `.eval-runs/live-v0.1.0/`，目录名对应
benchmark.json 的 artifacts 字段。文件未包含进 Git；在本机保留用于复核。

## 真实 MCP 与自维护

- 真实 stdio 示例 Server 被 DeepSeek 自动调用：2 轮模型、1 次 MCP 工具。
  [首次 MCP 报告](../validation/mcp-live.json) 中出现不存在的 E1，运行时明确告警，未伪装成有效引用。
- 自维护任务使用 RepoPilot 自己的源码副本，修复目录 git_diff 遇到二进制文件时整体失败的问题。
  [首次报告](../validation/self-maintenance-initial.json)：7 轮模型、8 次工具，根目录测试通过。
- 评审补充子目录测试后，恢复同一会话再运行：5 轮模型、6 次工具，三个回归测试通过，且最终
  引用无告警。完整证据见 [自维护与会话继续](../validation/self-maintenance.json)。
- 已审阅并合入其局部修复；没有直接让模型操作原始开发目录。固定 36 次评测针对上述来源版本，
  后续 git_diff 修复另外运行回归验证，未把基线冒充修改后的重跑结果。

## 本地与远端边界

本地运行了真实 Windows 子进程、Maven、pytest、MCP、文件写入、工作区锁、恢复和回退测试。
Ruff、格式检查、Windows/Linux 两种平台的 mypy 检查均通过。

GitHub Actions 已配置 Windows 与 Ubuntu；尚未推送仓库，远端 CI 和 Ubuntu 运行时未验证。
没有真实生产项目的大规模编码评测，也没有生产沙箱、RAG 或多智能体实现。

最终离线门禁为 113 项测试通过，Ruff 检查与格式检查通过。构建 wheel 与 sdist 后，在独立
虚拟环境中安装 wheel，确认导入来自该环境的 site-packages，六个命令均出现在帮助中，
无 API Key 时 doctor 与会话查询正常运行，且未调用模型。安装记录见
[install-smoke.json](../validation/install-smoke.json)，构建文件位于本地 dist/。

运行结果与源码学习能力分别验收；学习练习仍需使用者亲自完成。
