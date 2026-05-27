# 合规与数据安全

> 本文档定义平台的合规策略。对应评分要点：「信息采集合规：遵守目标站点 robots.txt 与服务条款，对外部数据来源有明确授权或公开声明 / 数据隐私与安全：用户访谈、问卷数据脱敏处理，无敏感信息泄露」。

---

## 1. 合规边界

平台采集的是**公开 B 端 SaaS 产品信息**：官网、定价页、帮助文档、博客、公开应用市场评论。**不采集**：

- 用户私人账号下的内容
- 需付费订阅才可见的内容（除非用户主动授权）
- 个人隐私信息（PII）
- 内部机密文档

---

## 2. robots.txt 合规

### 2.1 强制策略

Collector 在抓取前必须：

1. 拉取目标域的 `https://<host>/robots.txt`
2. 解析 User-agent 与 Disallow 规则
3. 当前 URL 命中 Disallow → 跳过 + 记录 `ROBOTS_BLOCKED`
4. 缓存 robots.txt 24h，避免每次请求

```python
# backend/tools/robots_checker.py
def is_allowed(url: str, user_agent: str) -> bool:
    parser = robotparser.RobotFileParser()
    parser.set_url(urljoin(url, "/robots.txt"))
    parser.read()
    return parser.can_fetch(user_agent, url)
```

### 2.2 用户覆盖

用户可在项目配置中设置 `respect_robots_txt=False`，但必须：
- 显示警告 + 输入"我已确认有合法授权"
- 该项目所有此类抓取标 `robots_overridden=True`
- 答辩 demo 默认开启 robots 合规

---

## 3. ToS（服务条款）合规

### 3.1 User-Agent

所有 HTTP 请求统一 User-Agent：

```
CompetitiveAnalysisBot/1.0 (+https://github.com/<repo>; contact@example.com)
```

可被服务方识别 + 联系。

### 3.2 抓取频率

- 单域名默认 1 req/s（可在 CollectConstraints 调整，但最高不超过 2 req/s）
- 触发 429 → 退避 60s
- 触发 403 持续 → 该域纳入暂停列表 24h

### 3.3 内容来源声明

报告生成时附"来源声明"：

```markdown
## 数据来源声明

本报告基于以下公开渠道于 2026-05-27 采集的资料生成：
- 各产品官网首页、定价页、帮助文档（具体 URL 见 evidence 附录）
- G2 / Capterra 用户公开评价
- 各产品官方博客、更新日志

采集遵循各站点 robots.txt 与公开服务条款。所有引用内容均提供原始来源链接。
报告中观点为基于上述资料的分析推断，不构成投资 / 采购建议。
```

由 Reporter 自动追加，模板见 `agents/reporter/prompts/source_disclaimer.md`。

### 3.4 不二次发布原始内容

- 报告中**引用**原文片段（< 200 字 / 段，符合合理使用）
- 不公开发布抓取到的完整 HTML / 文档全文
- 原始 HTML 仅保留在用户私有 evidence 库

---

## 4. 数据隐私（PII）

### 4.1 脱敏

所有写入 trace / 报告 / evidence 的内容过 `sanitizer`：

```python
PII_PATTERNS = [
    EMAIL_REGEX,
    PHONE_REGEX_CN, PHONE_REGEX_US,
    ID_CARD_REGEX,
    CREDIT_CARD_REGEX,
    SSN_REGEX,
]

def sanitize(text: str) -> str:
    for p in PII_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text
```

### 4.2 用户访谈 / 问卷数据

如果用户在项目中**主动上传**访谈记录 / 问卷数据：

- 入库前强制脱敏（去除受访者真实姓名 / 公司名 / 联系方式）
- 报告中引用时使用化名（"某位产品经理" / "受访者 A"）
- 用户上传时签同意书（前端 checkbox）

### 4.3 模型侧防泄露

- LLM Provider 默认关闭训练日志收集（Anthropic API 默认行为）
- 不在 prompt 中放真实凭据 / API key
- 文件上传不发往 LLM 默认开启时确认

---

## 5. 模型与工具使用合规

### 5.1 模型

- 默认 Claude（Anthropic 商业 API）
- 备选 DeepSeek / Qwen（国内商业 API）
- **禁止**使用免授权破解模型
- 模型使用条款随 LLMProvider 注册：

```python
PROVIDERS = {
    "anthropic": {
        "tos_url": "https://www.anthropic.com/legal/...",
        "commercial_use": True,
    },
    ...
}
```

### 5.2 第三方工具

- Tavily / Firecrawl：商业 API，付费订阅，符合 ToS
- Playwright：开源（Apache 2.0）
- Chroma：开源（Apache 2.0）
- LangGraph：开源（MIT）

依赖清单 + license 见 `LICENSES.md`（v1 完成时补全）。

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

删除遵循"软删 → N 天 → 硬删"，软删期间可恢复。

---

## 7. 安全实践

- 所有外部 API key 从环境变量读，不入仓
- `.env.example` 占位，真实 `.env` 加 `.gitignore`
- API 接入需 JWT / Session
- 数据库密码、模型 key 使用 secret manager（v2）
- 输出过 SQL injection / XSS 防护（FastAPI + Pydantic 默认安全）

---

## 8. 答辩材料合规清单

提交前必须检查：

- [ ] 演示项目使用的所有竞品对象信息均为公开
- [ ] 报告附数据来源声明
- [ ] 演示视频中不出现真实联系方式 / API key
- [ ] 代码仓库不含真实 `.env` 文件
- [ ] 答辩 PPT 引用第三方品牌 / Logo 注明来源
- [ ] 工具与模型使用符合「工具与资源使用规范」

---

## 9. 实现位置

```
backend/
├── tools/
│   ├── robots_checker.py
│   ├── rate_limiter.py            # 单域名速率
│   └── pii_sanitizer.py
├── observability/sanitizer.py     # trace 写入前
├── agents/reporter/prompts/
│   └── source_disclaimer.md
└── api/middleware/auth.py
```

`LICENSES.md`、`SECURITY.md` v1 阶段补全。
