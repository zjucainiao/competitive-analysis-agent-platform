# Reporter Agent · 报告撰写

> 详细契约见 [docs/AGENTS.md § 6](../../../docs/AGENTS.md#6-reporter报告撰写-agent)。

## 职责

把 `AnalysisResult` 渲染为结构化竞品分析报告（章节 / 段落 / 引用三层）。
**严格禁止引入未在 evidence / analysis 中出现的事实**。

## 输入 / 输出

- Input：`ReporterInput`（含 `analysis: AnalysisResult` + `template_id` + `target_audience`）
- Output：`ReporterOutput`（含 `draft: ReportDraft`）

## 支持的模板

模板用 Pydantic 字面量保存在 [templates.py](./templates.py)（避免引入 PyYAML 依赖）：

| `template_id`   | 目标读者       | 章节                                                    |
|-----------------|---------------|---------------------------------------------------------|
| `standard_v1`   | 产品经理       | 概览 / 核心功能 / 定价 / SWOT / 数据来源声明             |
| `investor_v1`   | 投资人         | 概览 / 定位 / 定价与变现 / SWOT-风险 / 数据来源声明      |
| `pm_v1`         | 产品规划经理   | 概览 / 能力差异 / 差异化机会 / SWOT / 数据来源声明        |

新增模板：复制其中一份 `ReportTemplate`，改 `template_id` 并注册到 `TEMPLATES`。

## 实现位置

```
backend/agents/reporter/
├── __init__.py             # 入口：Reporter / 工具 / 模板
├── agent.py                # 主体：mock + LLM 分章节 + 引用强制
├── templates.py            # 三个内置 ReportTemplate 字面量
├── tools.py                # 禁用词、数字提取、Evidence 提供者
├── fixtures.py             # 测试 / demo 输入加载器
├── prompts/
│   ├── system.md           # LLM 系统提示（引用强制 / 禁用词）
│   ├── section.md          # 章节生成 prompt 模板
│   └── source_disclaimer.md
├── README.md
└── tests/
    ├── conftest.py         # NullLLM / FakeLLM / NullTracer
    └── test_agent.py
```

## 关键约束（核心：引用强制）

| 校验位置                          | 触发错误码                  | 行为                                  |
|----------------------------------|----------------------------|---------------------------------------|
| 段落 `claim_ids` ⊆ 分析池         | `INSUFFICIENT_EVIDENCE`    | 抛 `AgentRunError` → status=NEEDS_REWORK |
| 段落 `evidence_ids` ⊆ 池          | `INSUFFICIENT_EVIDENCE`    | 同上                                  |
| 事实段无 evidence                 | `MISSING_CITATION`         | 同上                                  |
| 段落出现的任意数字找不到 evidence | `UNVERIFIED_QUANTITY`      | 同上（±5% 容差，不依赖 `is_quantitative` flag）|
| 段落事实陈述无法从 evidence 推出  | `UNVERIFIED_INFERENCE`     | 同上（LLM-as-judge，mock / 无 llm 时跳过）|
| 未注册 `template_id`              | `TEMPLATE_NOT_FOUND`       | status=FAILED（不抛，直接 fail output）|
| 禁用词命中（含数字+强力修饰模式） | —（warn 而非抛错）          | metadata.banned_term_hits++ + 降置信   |

### 数字 hallucination 拦截（R-1 / R-4 累积）

- `_post_validate` 与 `_llm_section_valid` 都扫**段落里出现的任何数字**，
  不再依赖 LLM 标的 `is_quantitative`（LLM 经常忘标）
- `_PLAIN_NUMBER_RE` 用 ASCII 字符断言，中文上下文里的"17 个集成 / 99 家
  企业 / 200 万用户"都会被抓出来核对
- Reporter 在段落入 `ReportDraft` 前自动校准 `is_quantitative` flag，
  方便下游 QA / Frontend 复用
- 模板可通过 `banned_terms_extra` 追加自定义禁用词

### 绝对化宣称拦截（R-4）

`tools.find_banned_terms` 同时检测固定禁用词 + 一组组合模式：

- 「\d+% xxx 信赖 / 采用 / 选择 / 认可」
- 「\d+ 强企业 / 公司 / 品牌」
- 「全网 / 全行业 / 所有 + 都/均/皆 + 在用/选/信赖」
- 「\d+ 家公司信赖 / 采用」

命中即计入 `metadata.banned_term_hits`，触发 status=PARTIAL + 降置信。

### 语义层 entailment 校验（R-4）

`Reporter(entailment_check=True)`（默认开启）使用 LLM-as-judge 对每个事实
性段落判定：段落事实陈述是否能从引用 evidence 直接推出。

- 输入：段落文本 + 引用 evidence 的 content 摘录
- 输出：`EntailmentVerdict { entailed: bool, reason: str }`
- 拦截典型场景：
  - 段落讲 A 时顺带断言 B，但只引用了 A 的 evidence
  - 段落用了 evidence 里没有的程度词（"完全缺失" / "远远落后"）
  - 从单条 evidence 推出多个结论，超出原文范围
- mock 模式 / `self.llm is None` / `entailment_check=False` → 自动跳过
- LLM 自身异常 / schema 不对 → 视为不阻塞（warn），避免 judge 故障搞翻
  整张报告

判定结果由 self-correct loop（R-5）消费（见下）；只有 `self_correct=False`
的兜底模式下，`_post_validate` 才直接 raise `UNVERIFIED_INFERENCE`。

### Self-correct loop（R-5，核心抑制幻觉机制）

`Reporter(self_correct=True)`（默认开启）在 ReportDraft 落定前内部修复脏
段落，避免发到 QA 的 draft 还带 hallucinated 数字 / 过度推断。

流程（在 `_build_output` 内、构造 ReportDraft 之前）：

```
LLM 写完所有 section
  ↓
检测每段：
  - extract_quantities + quantity_supported → 找出 evidence 里不存在的数字
  - entailment LLM-as-judge → 找出过度推断段落
  ↓
有脏段？ →（最多 MAX_REPAIR_ATTEMPTS=3 轮）
   对每段单独跑 _llm_repair_paragraph：
     - prompt 里点名要 drop 的具体数字 / 推断
     - LLM 用定性表述（"显著高于" / "若干"）重写
     - 重写完文本 → 下一轮重新检测（修一段可能破坏另一段）
  ↓
仍脏的段（LLM 修不掉）→ 强制兜底：
  - 仅数字问题：_strip_number_token 把每个坏数字替换成定性词
                + 段落标 is_soft_conclusion=True
  - entailment 失败：整段从 section 移除（section 空了补占位段）
  - 每次兜底记一条 SELF_CORRECT_FALLBACK warn
  ↓
重新统计 banned_term_hits / unverified_quantity_hits
  ↓
ReportDraft 装配，进入 _post_validate（此时段落已干净）
```

跟之前版本相比，避免了「LLM 编新数字 → QA reject → 反馈环重做 → LLM 又编一批」的 4 轮循环，整链路从 ~11 min 缩到 ~5 min。

**关键设计**：

- `_llm_section_valid` 在 `self_correct=True` 时跳过数字校验
  （否则一遇 hallucination 就 fallback heuristic、self-correct 没机会修）
- `_post_validate` 在 `self_correct=True` 时跳过 entailment 校验
  （已在 self-correct 处理过，避免重复调 LLM）
- entailment verdict 在 self-correct 内有 `(paragraph_id, text)` 缓存，
  text 没变就复用
- forced_fallback 计数进 metadata + confidence 惩罚（-0.08 per）+ status=PARTIAL
- 关闭 `self_correct=False` 时退化为 R-4 行为：`_post_validate` 直接 raise
  `UNVERIFIED_QUANTITY` / `UNVERIFIED_INFERENCE`，由外层反馈环重做

**成本**：

- entailment：每段 1 次 LLM 调用（缓存命中后免）
- repair：每个脏段 × 每轮 1 次 LLM 调用，最多 3 轮
- 实测真实链路下平均 ~12 次 LLM/报告，但省掉了反馈环 3 轮 × 80s 的重做

**metadata 新增字段**：

- `repair_attempts`：self-correct 跑了几轮
- `forced_fallbacks`：兜底处理的段落数（>0 → status=PARTIAL）

## 流程概览

1. 解析 `template_id` → `ReportTemplate`；不存在直接返回 FAILED
2. 计算 analysis 的 `claim_pool` 与 `evidence_pool`
3. 按模板章节顺序遍历：
   - `is_overview=True`：用 target / competitors / 维度生成概览段（soft）
   - `is_disclaimer=True`：用 `template.disclaimer` 生成单段（soft）
   - 其他：
     - 真实模式优先调 LLM（`response_format=ReportSection`），并跑一遍引用门禁
     - LLM 失败 / 不达标 → fallback 启发式（按 `AnalysisClaim.text` 平铺）
4. 后处理：禁用词扫描 + 数字校验软统计
5. `_post_validate`：硬门禁（见上表）
6. 评分：禁用词 / 未核数字 / 总段落数低于模板下限均降置信

## Evidence 提供者（数字校验关键）

数字校验需要 Evidence 原文。Reporter 通过 `EvidenceProvider` Protocol 拿数据：

- mock 模式：默认 `FixtureEvidenceProvider`，从
  `fixtures/mock_data/evidences/evidence_db.jsonl` 加载
- 真实模式：由 Orchestrator / I 窗口注入实现；**未注入时数字校验自动跳过**
  （不污染置信、不报 warn），等同于 `is_quantitative` 段落只走 `evidence_ids`
  非空校验

接口：

```python
class EvidenceProvider(Protocol):
    def get_many(self, evidence_ids: Iterable[str]) -> dict[str, Evidence]: ...
```

## 自评估

- `total_paragraphs < template.min_total_paragraphs` → -0.15
- 每命中一个禁用词 → -0.05
- 每个未核数字 → -0.1
- confidence < 0.6 → BaseAgent 强制 self_critique 非空

## 已知限制 / TODO

- v1：仅产 `ReportDraft` 结构化对象，markdown 渲染由前端 / Orchestrator 后处理
- v1：不支持 docx / PDF 输出
- v1：图表数据由 Analyst 的 `comparison_matrix` 提供，本 Agent 不再产图
- v2：多模板风格学习、用户自定义模板的运行时注册接口

## 责任窗口

**架构窗口 + R 窗口（合并）**，M0 后开始，M1 完成 v1。

## 运行 / 验证

```bash
# 单元测试
python3 -m pytest backend/agents/reporter/tests -q

# 一行 demo
python3 -c "
from backend.agents.reporter import Reporter
from backend.agents.reporter.fixtures import load_demo_input
agent = Reporter(mock=True)
inp = load_demo_input(template_id='standard_v1')
out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
print(out.status.value, len(out.draft.sections), out.draft.metadata)
"
```
