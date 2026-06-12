# 贡献指南

感谢你对本项目的关注。本文说明如何在本地搭好开发环境、跑测试、提交改动。

## 开发环境

完整的本地搭建步骤见 [README 的「本地部署」](README.md#本地部署)。最小开发依赖：

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # 含 pytest / ruff / mypy
cd frontend && npm install && cd ..
```

## 提交前自检

后端：

```bash
ruff check backend            # lint
ruff format --check backend   # 代码风格
mypy backend                  # 类型检查（尽量通过；遗留告警可在 PR 说明）

# 不发 LLM 请求的单元 + 集成测试（秒过，必跑）
pytest backend/orchestrator/tests backend/api/tests \
       backend/storage/tests/test_memory.py backend/storage/tests/test_serde.py -q
```

前端：

```bash
cd frontend
npm run lint
npm run build                 # 确保能产出
```

涉及真实链路的改动，建议另跑（需 API key，约 5–10 分钟）：

```bash
RUN_REAL_LLM_TESTS=1 pytest backend/api/tests/test_real_full_chain.py -v -s
```

## 代码与文档约定

- **Agent 间走结构化契约**：新增/修改 Agent 输入输出走 `backend/schemas/` 里的 Pydantic 模型，不要用自然语言对话传递。接口契约见 [docs/AGENTS.md](docs/AGENTS.md)、数据模型见 [docs/SCHEMA.md](docs/SCHEMA.md)。
- **两套编排引擎都要保持可用**：`native`（默认，`backend/orchestrator/graph.py`）与 `legacy`（`ORCH_ENGINE=legacy`）。改编排时两条路径都别破坏。
- **文档严格按代码写**：改了行为就同步改对应文档（每个子系统的设计文档由代码 docstring 反向引用，见各 `docs/*.md`）。不要在文档里写代码没有的东西。
- **不要提交密钥**：`.env` / `.env.prod` 已被 `.gitignore` 屏蔽；任何 API key、密码只放本地或部署机环境变量。

## 提交流程

1. 从 `main` 切出特性分支。
2. 小步提交，commit message 用祈使句、说清「做了什么 + 为什么」。
3. 自检（上面的 lint + test）通过后发 PR，描述改动范围与验证方式。

## 目录速览

```
backend/    FastAPI + LangGraph 多 Agent 编排（agents / orchestrator / schemas / storage / llm / api）
frontend/   Next.js 前端
scripts/    本地数据层等辅助脚本
docs/       设计与子系统文档
```
