# 合规与数据安全

> 本文档定义平台的合规策略：信息采集合规（遵守目标站点 robots.txt 与服务条款、对外部数据来源有明确声明）与数据隐私安全（敏感信息脱敏处理）。
>
> **状态标注约定**：每条控制项明确区分
> **[已实现]**（代码已落地，附 `file:line`）与 **[计划]**（仅设计/未实现，无对应代码）。
> 不要把计划项当成已交付能力宣传。

---

## 1. 合规边界

平台采集的是**公开 B 端 SaaS 产品信息**：官网、定价页、帮助文档、博客、公开应用市场评论。**不采集**：

- 用户私人账号下的内容
- 需付费订阅才可见的内容（除非用户主动授权）
- 个人隐私信息（PII）
- 内部机密文档

---

## 2. robots.txt 合规

### 2.1 强制策略 — [已实现]

Collector 抓取前执行（`RobotsChecker`，`backend/agents/collector/tools.py:117`）：

1. 拉取目标域的 `https://<host>/robots.txt`（`_fetch_robots`，tools.py:170）
2. 用 `urllib.robotparser` 解析 User-agent 与 Disallow 规则
3. 当前 URL 命中 Disallow → 跳过该 URL + 追加 `ROBOTS_BLOCKED` 错误
   （强制点在 `backend/agents/collector/agent.py:801-815`：
   `if inp.constraints.respect_robots_txt and robots is not None: ... code="ROBOTS_BLOCKED"`）
4. robots.txt 带 TTL 缓存 24h（`ROBOTS_CACHE_TTL = 60*60*24`，tools.py:37）
5. 无 robots.txt 或解析失败按「允许」处理（标准做法，tools.py:136）

实现签名（`RobotsChecker.is_allowed`，tools.py:133）：

```python
def is_allowed(self, url: str) -> bool:
    host = urlparse(url).netloc
    parser = self._get_parser(url)         # 带 24h TTL 缓存
    if parser is None:                     # 无 robots.txt → 允许
        return True
    return parser.can_fetch(self.user_agent, url)
```

> 注：本仓**没有** `backend/tools/robots_checker.py`。robots 检查在 collector 子包内。

### 2.2 用户覆盖

- **[已实现]** `CollectConstraints.respect_robots_txt`：为 `False` 时跳过 robots 检查
  （agent.py:801 的 `if inp.constraints.respect_robots_txt ...`）。
- **[计划]** 覆盖时的「显示警告 + 输入『我已确认有合法授权』」确认流程、
  对此类抓取打 `robots_overridden=True` 标记 —— 当前**无对应代码**（无 `robots_overridden`
  字段、无确认提示）。

---

## 3. ToS（服务条款）合规

### 3.1 User-Agent — [已实现]

所有 HTTP 请求统一 User-Agent（`USER_AGENT`，`backend/agents/collector/tools.py:30`）：

```
CompetitiveAnalysisBot/1.0 (+https://github.com/example/competitive-analysis-agent-platform; contact@example.com)
```

可被服务方识别 + 联系。`RobotsChecker` 与抓取 client 都默认带此 UA。

### 3.2 抓取频率

> 【锚点保留：collector/tools.py:35 引用本节】

- **[已实现]** 单域名默认 **1 req/s** 节流：`DomainRateLimiter`
  （`backend/agents/collector/tools.py:95`，常量 `DEFAULT_MIN_DOMAIN_INTERVAL = 1.0`，tools.py:35）
  对每个 host 强制最小间隔，线程安全、并发接力等待；
  强制点在 `backend/agents/collector/agent.py:816`（`limiter.acquire(host)`）。
- **[计划]** 「可在 `CollectConstraints` 调整但 ≤ 2 req/s 上限」「触发 429 → 退避 60s」
  「触发 403 持续 → 该域暂停 24h」—— **均无对应代码**：限速器只有固定最小间隔，
  collector 全仓无 429/403/backoff/suspend 逻辑（`grep -niE '429|403|backoff|suspend'
  backend/agents/collector/` 无命中）。

### 3.3 内容来源声明 — [已实现]

> 【锚点保留：frontend/src/lib/report-export.ts:70 引用本节】

报告生成时附「数据来源声明」段（示例）：

```markdown
## 数据来源声明

本报告基于公开渠道采集的资料生成：
- 各产品官网首页、定价页、帮助文档（具体 URL 见 evidence 附录）
- 公开应用市场 / 评价站点的用户公开评价
- 各产品官方博客、更新日志

采集遵循各站点 robots.txt 与公开服务条款。所有引用内容均提供原始来源链接。
报告中观点为基于上述资料的分析推断，不构成投资 / 采购建议。
```

由 Reporter 自动追加为报告最后一节：`disclaimer` 字段定义在
`backend/agents/reporter/templates.py:53`（「数据来源声明，自动追加为最后一节的段落」），
各模板的「数据来源声明」小节见 templates.py:121 等；提示词模板
`backend/agents/reporter/prompts/source_disclaimer.md`。前端导出时也会按本节注入声明
（`frontend/src/lib/report-export.ts:70`）。

### 3.4 不二次发布原始内容 — [策略，未在代码强制]

- 报告中**引用**原文片段（< 200 字 / 段，符合合理使用）
- 不公开发布抓取到的完整 HTML / 文档全文
- 原始 HTML 仅保留在用户私有 evidence 库

> 这是产品/运营策略；当前**无代码层面的引用长度上限校验或公开发布拦截**。

---

## 4. 数据隐私（PII）

### 4.1 脱敏

> 【锚点保留：backend/tools/sanitizer.py:3 与 backend/observability/tracer.py:21 引用本节】

- **[已实现]** PII 脱敏器 `Sanitizer`（`backend/tools/sanitizer.py:110`，**不是**
  `pii_sanitizer.py`）。覆盖模式（`DEFAULT_PII_PATTERNS`，sanitizer.py:47）：
  邮箱、信用卡（13-19 位带分隔符）、美国 SSN、中国身份证 18 位、中国手机号、北美电话、
  OpenAI 风格 `sk-` API key、`Bearer` token。默认替换 token `[REDACTED]`，
  `redact_label=True` 时带类型标签 `[REDACTED:EMAIL]`。
- **[已实现] 但接入面有限**：脱敏目前**只接在可观测性链路**——
  `backend/observability/tracer.py:104,181`（写 OTLP span attribute 前过 `sanitize`）和
  `backend/observability/io_snapshot.py:33`（节点 I/O 快照）。
- **[计划]** 把脱敏接进**报告 / evidence 写库链路**——
  当前报告与 evidence **不过** `sanitize`（无对应调用点）。本节标题历史上写的「所有写入
  trace / 报告 / evidence 的内容过 sanitizer」只在 trace 这一侧为真。

实现签名（sanitizer.py:134）：

```python
class Sanitizer:
    def __init__(self, patterns=DEFAULT_PII_PATTERNS, *, redact_label=False): ...
    def sanitize(self, text: str) -> str: ...                # 命中替换为 [REDACTED]
    def sanitize_with_stats(self, text): -> tuple[str, SanitizationStats]  # 审计命中分布
# 模块级便捷入口：backend.tools.sanitize(text)
```

### 4.2 用户访谈 / 问卷数据 — [计划]

设想：用户在项目中**主动上传**访谈/问卷数据时——

- 入库前强制脱敏（去除受访者真实姓名 / 公司名 / 联系方式）
- 报告中引用时使用化名（"某位产品经理" / "受访者 A"）
- 用户上传时签同意书（前端 checkbox）

> 当前**无代码**：既无访谈/问卷上传链路，也无对上传内容调用 `sanitize`。属未实现设计。

### 4.3 模型侧防泄露

- **[已实现]** 不在 prompt 中放真实凭据 / API key（key 全部从环境变量读，见 § 7）；
  `Sanitizer` 的 `sk-` / `Bearer` 模式可在可观测性侧兜底过滤已外泄到文本里的凭据。
- **[计划/外部依赖]** 「LLM Provider 关闭训练日志收集」依赖各 Provider 账号侧设置，
  非本仓代码可保证。默认 Provider 为豆包 / DeepSeek / OpenAI（见 § 5.1），不是 Anthropic。
- **[计划]** 「文件上传发往 LLM 前确认」—— 无对应代码（无文件上传链路）。

---

## 5. 模型与工具使用合规

### 5.1 模型 — [已实现]（默认 Provider）

- 默认装配顺序：**豆包（火山方舟）优先 → DeepSeek → OpenAI 兜底**
  （`build_llm_from_env`，`backend/llm/__init__.py:60-79`，docstring 原文「豆包优先，
  DeepSeek / OpenAI 兜底」）。Provider 统一走 OpenAI 兼容客户端 `OpenAICompatibleLLM`，
  按环境变量 `DOUBAO_*` / `DEEPSEEK_*` / `OPENAI_*` 选档。
- **不是默认 Claude/Anthropic**：Anthropic 仅出现在计费价目表
  `backend/llm/pricing.py:38-43`（`claude-*` 价格），无 Anthropic 运行时 Provider 装配。
- **禁止**使用免授权破解模型（策略）。

> **[计划]** 「模型使用条款随 LLMProvider 注册（`PROVIDERS` dict 带 `tos_url` /
> `commercial_use`）」—— **无对应代码**：仓内无此 ToS 注册表。

### 5.2 第三方工具

- **[已实现/已用]** Tavily（`backend/agents/collector/tools.py`，REST API，需 `TAVILY_API_KEY`）；
  httpx + readability 抓取兜底。
- **[声明依赖]** Playwright（开源 Apache 2.0）：collector 仅声明 fallback 接口；
  Firecrawl（商业 API）：作为抓取首选项之一。
- **[计划]** LangGraph（开源 MIT）：编排器选型方向。
- **[未使用]** `chromadb` 在 `pyproject.toml` 声明但全仓**无 import**（无向量库依赖落地），
  此前「Chroma 开源依赖」一行已删，避免宣称未用依赖。

> 依赖清单 + license：仓内**目前没有** `LICENSES.md`（计划项，未创建）。

---

## 6. 数据保留与删除

| 数据 | 默认保留期 | 用户可控 |
|---|---|---|
| 报告 | 永久 | 可手动删除 |
| Evidence | 永久 | 可手动删除（连带 claim 失效） |
| Trace | 90 天 | 可设置 |
| LLM messages / responses | 30 天 | 大字段 |
| 原始 HTML | 30 天 | 大字段 |
| 用户上传文件 | 用户控制 | 必须明示同意保留期 |

> 上表为**保留策略目标**；多数保留期/自动过期尚无定时清理代码实现。

删除流程：

- **[已实现] 软删**：项目删除写 `deleted_at`（进回收站，可恢复）——
  `backend/api/routes/projects.py:176`（`"deleted_at": datetime.now(timezone.utc)`），
  恢复时清回 `deleted_at=None`（projects.py:161）。
- **[计划] 30 天后硬删的 cron**：**未实现**——projects.py:172 明确注释
  「30 天保留期由外部 cron 真删（v1 不实施 cron）」。所以「软删 → N 天 → 硬删」
  目前只走到「软删」这一步。

---

## 7. 安全实践

- **[已实现]** 所有外部 API key 从环境变量读，不入仓；`.env.example` 占位，真实 `.env` 进 `.gitignore`。
- **[已实现]** API 鉴权 = **JWT(HS256) + bcrypt 密码哈希**：
  - 登录/注册：`backend/api/routes/auth.py`
  - 密码哈希 + JWT 签发/校验：`backend/api/security.py`（`bcrypt` + `pyjwt`，JWT 只放
    `sub`=user_id 与 `exp`）
  - 受保护路由依赖：`backend/api/deps.py:37` 的 `get_current_user`（解析
    `Authorization: Bearer <jwt>`，失败 401）
  - JWT 密钥从 `JWT_SECRET` 读：postgres 形态（生产判据）缺失时启动直接拒绝
    （`security.py` 的 `ensure_jwt_secret`，可用 `JWT_ALLOW_INSECURE_DEV=1` 显式豁免）；
    memory 形态回退开发默认值并告警
  - 注意：是 JWT，**不是 Session**；仓内**无** `backend/api/middleware/` 目录。
- **[计划]** 数据库密码、模型 key 使用 secret manager（v2）。
- **[部分已实现]** SQL injection / XSS：后端用 SQLAlchemy 参数化 + Pydantic 校验输入
  （非手工拼 SQL）；XSS 防护取决于前端渲染，未单列防护层。

---

## 8. 实现位置

> 实际落地位置（真实路径）：

```
backend/
├── agents/collector/
│   ├── tools.py                    # robots（RobotsChecker, :117）+ 单域名限速
│   │                               #   （DomainRateLimiter, :95）+ UA（:30）
│   └── agent.py                    # robots/限速强制点（:801 / :816）
├── tools/
│   └── sanitizer.py                # PII 脱敏 Sanitizer（:110）—— 不是 pii_sanitizer.py
├── observability/
│   ├── tracer.py                   # OTLP span 写入前过 sanitize（:104,:181）
│   └── io_snapshot.py              # 节点 I/O 快照过 sanitize（:33）—— 无 observability/sanitizer.py
├── agents/reporter/
│   ├── templates.py                # 数据来源声明 disclaimer 字段（:53）
│   └── prompts/source_disclaimer.md
└── api/                            # 鉴权（无 middleware/ 目录）
    ├── routes/auth.py              # 登录/注册
    ├── security.py                 # bcrypt + JWT 签发/校验
    └── deps.py                     # get_current_user（Bearer JWT → User）
```

**已知不存在 / 计划项（避免误引为现有文件）**：

- 无 `backend/tools/robots_checker.py`、无 `backend/tools/rate_limiter.py`、
  无 `backend/tools/pii_sanitizer.py`、无 `backend/observability/sanitizer.py`、
  无 `backend/api/middleware/`（robots/限速/脱敏的真实位置见上）。
- 无 `LICENSES.md`、无 `SECURITY.md`（计划补全，当前未创建）。
- `robots_checker` / `rate_limiter` / `pii_sanitizer` 当前在 collector 内自给，未抽到 `backend/tools/`（真实位置见上）。
