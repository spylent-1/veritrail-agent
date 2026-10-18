# VeriTrail 发布检查（中文）

日期：2026-10-18（Asia/Shanghai）。目标仓库：`spylent-1/veritrail-agent`。

## 可信度修复

- [x] 每次成功写入使旧验证失效，changed_files 去重；拒绝、失败和无变化不冒充成功写入。
- [x] EvidenceRef 只覆盖实际返回的完整行；超长部分行和空文件无虚构引用；截断不新增读取授权。
- [x] Git 差异包含删除、暂存、未暂存及未跟踪；历史和索引内容也接受敏感文件、大小、编码与链接检查。

分别验收并提交：`8f8529a`、`a01ff6e`、`afbb0d0`；修复阶段累计 144 tests 通过。

## 仓库与包装

- [x] `.gitignore` 覆盖虚拟环境、缓存、IDE、构建、覆盖率、日志、私有 SQLite/快照/评测及常见密钥。
- [x] 保留 `.env.example`、`uv.lock` 和 `docs/validation/*.json` 验收摘要。
- [x] VeriTrail / veritrail-agent 品牌，veritrail 主命令及 repopilot 兼容入口。
- [x] 英文首页、中文首页及双语架构、安全、验收说明；历史进度与学习路线标注中文。
- [x] MIT 许可证及对应包元数据；历史真实模型报告保留原始来源含义。

## 发布验收

最终门禁、源码指纹、构建与独立安装结果见 [发布验收摘要](validation/publication.json)。
有限凭证模式扫描不能替代完整安全审计；原始运行工件不进入 Git。
验证必须对应同一源码内容，远端状态以最终提交关联的
[Windows / Ubuntu Actions](https://github.com/spylent-1/veritrail-agent/actions/workflows/ci.yml) 为准。

```powershell
python -m uv run pytest
python -m uv run ruff check .
python -m uv run ruff format --check .
python -m uv run mypy
python -m uv build
git diff --check
git status --short
git diff --cached --name-status
```

推送只使用普通 main 更新，保留本地历史。CI 通过前不声明远端验证完成。
本次不发布 PyPI、不创建正式 Release，也不新增付费模型评测。
