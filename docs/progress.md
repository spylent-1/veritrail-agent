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
| T13–T14 MCP | 待实现 | |
| T15–T16 评测和交付 | 待实现 | |

不包含 RAG、长期 Memory、Skill、并行 Agent、自由 Shell 和生产沙箱。

T05 实测说明：deepseek-flash，thinking disabled；正确定位 Controller → Service → Mapper，
引用对应实际读取的三份 Java 源码。样例是受控的普通 Java 调用链，不代表大型 Spring Boot
仓库评测或生产代码质量结论。真实测试执行尚未引入，因此 verification=not_run。

Provider SDK 契约参考：https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create
