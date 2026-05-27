# 编码与协作规范

> 本文档定义代码风格、目录结构、Git 工作流。所有窗口必读。

---

## 1. 仓库结构（再次明确）

```
.
├── backend/
│   ├── agents/<name>/         # 各 Agent 独立目录
│   │   ├── __init__.py
│   │   ├── agent.py           # 实现 Agent 主类
│   │   ├── prompts/           # 所有 prompt 外置
│   │   ├── tools.py           # 本 Agent 专属工具调用
│   │   ├── README.md          # 本 Agent 说明
│   │   └── tests/
│   ├── orchestrator/
│   ├── schemas/               # Pydantic 模型（架构窗口维护）
│   ├── llm/                   # LLMProvider 抽象
│   ├── tools/                 # 通用工具（chunker / robots / rag / ...）
│   ├── storage/               # PG / Chroma / Redis 适配
│   ├── observability/
│   ├── api/                   # FastAPI 路由
│   └── tests/                 # 集成测试
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/               # 自动生成的 TS 类型
│   │   └── hooks/
│   └── package.json
├── fixtures/mock_data/
├── docs/
├── pyproject.toml / uv.lock
├── docker-compose.yml         # v1 完成时补
├── .env.example
├── .gitignore
└── README.md
```

---

## 2. Python 规范

### 2.1 语言与版本

- Python 3.12+
- 类型注解全覆盖（包括函数返回 None 也要写）
- 包管理 `uv`

### 2.2 风格

- **格式化**：`ruff format`（行宽 100）
- **lint**：`ruff check`
- **静态检查**：`mypy` 严格模式（v2 启用，v1 至少 `--strict-optional`）

### 2.3 Pydantic

- 业务模型用 Pydantic v2
- 禁止 `dict[str, Any]`（除 trace_record 的快照字段）
- 字段名 snake_case
- 枚举用 `str, Enum` 双继承（便于序列化）

### 2.4 命名

```
模块名:   snake_case          backend/agents/collector/
类名:    PascalCase           class Collector(BaseAgent):
函数:    snake_case           def collect_pages():
常量:    UPPER_SNAKE         MAX_TOKENS = 4096
私有:    _leading_underscore  def _internal_helper():
```

### 2.5 Prompt 外置

```python
# 不允许
SYSTEM = "You are an analyst. Output JSON..."

# 必须
SYSTEM = (PROMPT_DIR / "system.md").read_text()
```

Prompt 文件命名：`<purpose>.md` 或 `<purpose>.j2`（用 Jinja2 模板）。

### 2.6 错误处理

```python
# 不允许：吞异常
try:
    result = risky_call()
except Exception:
    pass

# 必须：明确处理 + 入 trace
try:
    result = risky_call()
except SpecificError as e:
    self.tracer.span_error(e)
    raise AgentError(code="...", message=str(e), retriable=True)
```

### 2.7 LLM 调用

```python
# 不允许
from anthropic import Anthropic
client = Anthropic()
client.messages.create(...)

# 必须
output = self.llm.chat(
    system=SYSTEM,
    messages=[...],
    response_format=MyModel,
    temperature=0.1,
)
```

---

## 3. TypeScript / 前端规范

- React 18 + TypeScript 5 + Next.js 14 App Router
- 类型从后端 OpenAPI 自动生成（`openapi-typescript`）
- 样式：Tailwind + shadcn/ui
- 状态：TanStack Query（服务端状态） + Zustand（本地）
- 格式化：`prettier`
- lint：`eslint --max-warnings 0`

---

## 4. 测试

### 4.1 Python

```
backend/agents/<name>/tests/
├── test_agent.py           # 单元测试
├── test_prompts.py         # prompt 校验（golden file）
└── fixtures/               # 测试用 fixture
```

每个 Agent 至少 3 个 case：正常 / 边界 / 异常。

### 4.2 集成

```
backend/tests/
├── test_orchestrator_e2e.py    # 跑完整 DAG
├── test_qa_feedback_loop.py    # 真实闭环测试
└── test_hallucination.py       # 幻觉抑制测试
```

### 4.3 跑测试

```bash
uv run pytest -m "not slow"     # 默认跳过慢测
uv run pytest -m e2e            # 仅集成
uv run pytest --cov=backend     # 覆盖率
```

---

## 5. Git 工作流

### 5.1 分支模型

```
main          ← 受保护，仅通过 PR 合入
├── architect/<topic>     架构窗口（docs / schemas / base）
├── agent/<name>/<topic>  Agent 窗口（如 agent/collector/init）
├── orchestrator/<topic>
├── frontend/<topic>
└── infra/<topic>
```

### 5.2 Commit 规范（Conventional Commits）

```
<type>(<scope>): <subject>

<body>

<footer>
```

`type`：
- `feat`：新功能
- `fix`：bug 修复
- `refactor`：重构（无功能变化）
- `docs`：文档
- `test`：测试
- `chore`：依赖、构建、工具
- `schema`：Schema 变更（必须！便于追踪）

例：
```
schema(profile): add ai_capabilities to FeatureProfile

为了支持 AI 能力专项对比，在 FeatureProfile 中新增 ai_capabilities 字段。
向后兼容：默认空 list。

SCHEMA_VERSION: 1.0.0 → 1.1.0
Affected: extractor, analyst, reporter
```

### 5.3 PR 规范

PR 描述包含：
- 变更内容
- 影响面（哪些窗口受影响）
- 测试结果（pytest 通过截图 / 关键日志）
- 截图（前端 PR）
- 是否变更 Schema（变更则 review 必须有架构窗口）

### 5.4 Review

- 架构窗口对所有 PR 有审查权
- Schema 相关 PR 必须经过架构窗口
- Agent 相关 PR 由对应 Agent 窗口主导，架构窗口校验契约符合度
- 集成 PR 必须包含 e2e 测试通过证据

---

## 6. 配置管理

### 6.1 环境变量

```
# .env.example（提交到仓库）
# === LLM ===
LLM_DEFAULT_PROVIDER=anthropic    # anthropic / deepseek / openai / qwen
ANTHROPIC_API_KEY=sk-...
DEEPSEEK_API_KEY=
OPENAI_API_KEY=

# === Tools ===
TAVILY_API_KEY=
FIRECRAWL_API_KEY=

# === Storage ===
DATABASE_URL=postgresql://...
CHROMA_PERSIST_DIR=./.chroma
REDIS_URL=redis://localhost:6379

# === App ===
APP_MODE=hybrid                  # mock / hybrid / real
LOG_LEVEL=INFO
TRACE_RETENTION_DAYS=90

# === Observability (Optional) ===
LANGCHAIN_API_KEY=
LANGCHAIN_TRACING=false
```

`.env`（真值）必须 `.gitignore`。

### 6.2 配置加载

```python
# backend/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    llm_default_provider: str
    anthropic_api_key: str | None = None
    ...
    class Config:
        env_file = ".env"

settings = Settings()
```

不允许在代码里写 `os.environ["KEY"]`，必须走 `settings`。

---

## 7. Schema 变更流程（重要）

1. 在 `backend/schemas/` 提变更 PR
2. PR 描述说明：变更动机 / 影响面 / 迁移路径
3. `SCHEMA_VERSION` bump（major / minor / patch）
4. 架构窗口审查
5. 合入主分支后：在群里 / 文档里通知所有受影响窗口
6. 受影响窗口在 1 天内同步更新

破坏性变更需要至少 24 小时缓冲期。

---

## 8. 文档要求

每个 Agent 目录必须有 `README.md`，包含：

- 本 Agent 做什么
- 输入输出 Schema（引用 docs/AGENTS.md 对应章节）
- 关键 prompt 与策略
- 运行方式（含 mock 模式）
- 已知限制
- TODO

文档变更（docs/*.md）的 PR 也走 review，避免文档腐烂。

---

## 9. 性能与成本

- **token 预算**：单 Agent 调用默认 ≤ 8K input + 4K output；超出需在 PR 描述说明
- **缓存**：相同 prompt + temperature=0 的 LLM 调用走 LLMProvider 缓存（默认 1h）
- **限流**：所有外部调用通过 `RateLimiter`，单 provider 默认 5 RPS

---

## 10. 安全清单

每次 PR 自检：

- [ ] 无 hardcoded API key / 密码
- [ ] 无 `print` 日志真实 PII
- [ ] LLM 调用不直接 import vendor SDK（必须走 LLMProvider）
- [ ] Pydantic 模型不用 `Any` 兜底
- [ ] 新增依赖在 `pyproject.toml` 声明且 license 兼容

---

## 11. AI 编程工具使用痕迹

评分要求：「TRAE 等 AI 编程工具的使用痕迹清晰，体现深度协作」。要求：

- 在 PR 描述中说明用了哪个 AI 工具协助（TRAE / Claude Code / Cursor）
- 关键 prompt / 决策记录保留在 `docs/prompts/` 目录（用 markdown 存）
- Commit 信息可包含 `Assisted-By: Claude` / `Assisted-By: TRAE` 等 footer

---

## 12. 关键路径之外的轻量约定

- 单文件 ≤ 500 行，超出拆模块
- 单函数 ≤ 60 行，超出拆函数
- 圈复杂度 ≤ 10
- 没有 TODO 注释超过 2 周不处理（合并前清理）
- 注释只写 **why**，不写 **what**

---

## 13. 速查：新窗口加入项目第一步

1. 读 [README.md](../README.md)
2. 读 [ARCHITECTURE.md](ARCHITECTURE.md)
3. 读 [WORKBREAKDOWN.md](WORKBREAKDOWN.md) 找到自己的角色
4. 读 [AGENTS.md](AGENTS.md) 对应 Agent 章节（如果是 Agent 窗口）
5. 读 [SCHEMA.md](SCHEMA.md) 对应数据模型
6. 读本文档（CONVENTIONS.md）
7. 在自己分支上动手

预计阅读时间 60–90 分钟。
