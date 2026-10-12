# 源码阅读路线与面试练习

不要一开始通读所有文件。用一个测试驱动一条调用链，每读完一段，自己改一个小条件并观察失败。

| 顺序 | 阅读入口 | 自己完成的练习 | 应能解释的问题 |
|---|---|---|---|
| 1 | models.py、providers.py、tests/test_protocol.py | 手写三轮 ScriptedFake | tool_call_id 如何配对？一个响应含多个调用怎么办？ |
| 2 | agent.py、tests/test_agent.py | 把轮次上限改小，解释停止原因 | 为什么 final answer 不等于任务成功？ |
| 3 | workspace.py、tools.py、tests/test_workspace.py | 加入一种敏感文件规则及测试 | Windows junction 与普通相对路径分别怎样越界？ |
| 4 | safety.py、editor.py、tests/test_editor.py | 在审批时改动文件 | 为什么批准后还要检查文件哈希？ |
| 5 | process.py、test_runner.py | 制造零测试、超时和输出过大 | 退出码为 0 为什么还不能直接写“测试通过”？ |
| 6 | store.py、recovery.py、tests/test_recovery.py | 模拟写入后、结果记录前中断 | 为什么不能直接重试上一条工具？ |
| 7 | context.py、tests/test_context.py | 超限后检查原始历史是否变化 | 为什么不能只取最后 N 条消息？ |
| 8 | mcp_connection.py、mcp_adapter.py | 给示例 Server 增加一个只读工具 | 工具注解为什么不能决定权限？ |
| 9 | evals/grading.py、evals/run.py | 分析一个真实失败报告 | 如何防止修改可见测试来伪造成功？ |

Python 对照：Protocol 类似轻量接口；Pydantic 类似有边界校验的 DTO；async/await 是明确
协作式等待，不会自动并行；async with 负责可靠清理资源；SQLite with db 是事务，不是关闭连接。

每次交给 Codex 的任务应说明允许修改的模块、输入输出与验收，并要求报告实际运行的检查。
开发阶段的本地提交见 `git log --oneline`，证据边界见 progress.md。

面试准备时请用你实际运行过的 Trace 说明：模型产生请求、工具校验、具体审批、意图记录、执行、
结果回传与下一轮请求。性能和准确率只引用 validation.md 对应的固定样本，不外推到通用编码能力。
