# V2.0 计划实施记录

产品版本：0.1.0。按 T01–T16 顺序推进；离线、真实子进程、真实模型和远端 CI 分别记证据。

| 任务 | 状态 | 验收 |
|---|---|---|
| T01 工程、配置、CLI、doctor、CI | 本地验收通过 | 5 tests；Ruff / format / mypy 通过；doctor 无远端请求 |
| T02 工作区边界 | 本地验收通过 | 累计 31 tests；真实 Windows junction、越界、敏感过滤、BOM/CRLF；质量门禁通过 |
| T03 消息与 Provider 契约 | 本地验收通过 | 累计 37 tests；消息配对、重复 ID、ScriptedFake；质量门禁通过 |
| T04 读取工具与统一执行入口 | 本地验收通过 | 累计 41 tests；真实 rg、参数/权限、密钥过滤；质量门禁通过 |
| T05 只读循环与 DeepSeek | 本地与真实调用通过 | 49 tests；真实 Java 样例 3 轮模型、5 次工具；输入 5756 / 输出 1637 tokens；运行 bd4ecab3dc8549878cc1bfc9a48811f8 |
| T06 会话与执行记录 | 本地验收通过 | 累计 52 tests；SQLite 往返、Trace、证据检查、工作区锁；质量门禁通过 |
| T07 宿主审批与操作意图 | 本地验收通过 | 累计 58 tests；拒绝无副作用、审批绑定、日志失败阻止执行 |
| T08 局部编辑、新建、快照与差异 | 本地验收通过 | 累计 65 tests；真实原子写入、BOM/CRLF、拒绝/冲突、保留原有修改 |
| T09 pytest 与受限子进程 | 本地验收通过 | 累计 74 tests；真实通过/失败/零测试/跳过；超时、取消、输出上限、子进程不继承模型密钥 |
| T10 Maven 适配 | 本地验收通过 | 真实 Windows mvn.cmd、空格/中文路径、JUnit 3 tests 失败→修复→通过；Shell 字符拒绝；新鲜报告校验 |
| T11 恢复与回退 | 本地验收通过 | 写入前/后/冲突、测试未知、审批/读取失效；多次编辑逆序回退并保留原有内容 |
| T12 上下文与运行预算 | 本地验收通过 | 上下文含工具 Schema 容量、完整轮次裁剪、历史不重写；审批等待不计预算、取消闭合消息、失败请求 usage 不冒充完整 |
| T13 stdio 生命周期 | 本地验收通过 | mcp 2.3.0；独立真实 Server 启动/发现/调用/关闭；异常启动结构化失败；legacy framing 无隐式工具重试 |
| T14 MCP 统一工具执行 | 本地与真实调用通过 | 真实模型 2 轮、1 次 MCP 工具；审批/allow-list/Schema/Trace、超时/断连；外部内容不伪装文件证据 |
| T15 评测与独立评分 | 本地验收通过 | 12 固定任务、每任务 3 次；独立 Python/Java 评分器分别拒绝 6 个故障版本、接受修复版本；固定起始提交 |
| T16 真实评测与交付 | 本地与真实调用验收通过 | 113 tests；Ruff / format / Windows 与 Linux 平台 mypy 通过；wheel/sdist 构建、独立环境安装与 CLI 验证通过；36 次真实评测定位 15/18、修复 18/18；真实自维护＋会话继续通过 |

不包含 RAG、长期 Memory、Skill、并行 Agent、自由 Shell 和生产沙箱。

T05 实测说明：deepseek-flash，thinking disabled；正确定位 Controller → Service → Mapper，
引用对应实际读取的三份 Java 源码。样例是受控的普通 Java 调用链，不代表大型 Spring Boot
仓库评测或生产代码质量结论。真实测试执行尚未引入，因此 verification=not_run。

Provider SDK 契约参考：https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create

T14 首次真实 MCP 回答引用了不存在的 E1，运行报告准确标为 Unknown evidence citation。
该原始结果保存在 docs/validation/mcp-live.json。系统提示已明确区分外部来源和文件证据，
但不能据此宣称模型不再产生错误引用；文件定位由评测评分器严格检查实际引用与当前哈希。

## 交付后的改进

2026-10-13：C01 连续对话入口，本地验收通过。新增 `repopilot chat`，逐条任务共用保存的会话，
支持 `/help`、`/new`、`/exit` 和 `chat --session`。终端输入仅在 CLI 内处理，继续调用原有
run_task、ToolExecutor、审批和 SQLite 路径；不复用旧审批或文件读取授权，不自动重放失败任务。
MCP 仍需显式选择，启动与调用继续逐次审批。

新增 12 项 ScriptedFake CLI 行为测试，覆盖历史与工具消息配对、新会话、退出/EOF、会话继续、
工作区冲突、拒绝编辑、失败后追问、取消与客户端清理、非交互拒绝及缺密钥。
全项目 125 tests 通过（39.45 秒），Ruff 检查/格式检查、mypy、git diff --check 通过；
真实已安装 CLI 的 chat 帮助可用。本次未调用真实模型，也未运行远端 CI；T16 的模型评测数据
保持原来源版本含义，不将其记为此次交互入口的真实模型验证。
