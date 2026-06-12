# PRODUCT.md

> 前端设计的上下文锚。与 DESIGN.md 一起构成前端设计契约。

---

## Register

**product**（app UI / dashboard / tool — 设计**服务于**产品能力，不是产品本身）。

后端的多 Agent 编排、证据链、Schema 是真正的产品；前端的职责是把这些能力变成产品经理 / 分析师能用、能信、能演示的界面。所以前端不追求"被记住的视觉"，追求"高效率 + 高可信 + 高密度 + 不打扰"。

---

## Users

**核心用户**：B 端 SaaS 产品经理 / 市场分析师 / 创业团队。

具象画像：

- **产品经理 Lily**：32 岁，在一家 200 人 SaaS 公司做 PM。每周需要跟踪 5-8 个竞品的版本更新、定价变化、用户评价。Notion / Linear 重度用户，对效率工具挑剔，讨厌"花里胡哨但没用"的 dashboard。每天在 27 寸显示器前坐 8 小时，习惯键盘快捷键。
- **市场分析师 Marcus**：38 岁，咨询公司高级顾问。给客户做季度竞品报告，需要可溯源的结论 + 漂亮的可导出材料。客户付费高，对"AI 编的"非常警觉，反复追问"这条结论从哪里来"。
- **创业团队 Founder Liu**：28 岁，AI 应用方向创业者。融资 / 产品规划阶段做差异化分析，要的是**快**+**有据可查**，不要花哨。

**操作环境**：办公室 27 寸或 14 寸笔记本，桌面端为主，移动端只看不操作。中英文混合内容场景。

**心智状态**：分析师怀疑 LLM 输出 → 必须用证据说服她。产品经理时间紧 → 必须低交互成本就能拿到结论。

---

## Product Purpose

把"竞品分析"这一过去依赖人工经验的工作流，转化为**可编排、可复用、可审查、可量化**的智能化流程。

不替代分析师的最终判断，而是提供：
- **结构化竞品知识**（按统一 Schema 沉淀，可对比可复用）
- **证据链可溯源**（每个结论可一键追到原文）
- **全过程可观测**（5 个 Agent 怎么做出这个结论，时间轴可回放）
- **质检反馈闭环**（系统自检 + 用户可介入修正）

对用户而言，最核心的承诺：**"这份报告里的每句话你都可以挑战。"**

---

## Brand

- **名称**：竞品分析 Agent 协作平台（v1 暂无正式品牌名，可用 "Competisense" / "Atlas" 等候选名）
- **气质**：克制、专业、可信、有工程美感。**不是消费级 SaaS 的"友好亲切"，是分析师工具的"沉静有据"**。
- **参考产品**（设计气质对标）：
  - **Linear**：信息密度 + 键盘优先 + 高对比深色
  - **Stripe Dashboard**：数据可视化质感 + 严谨的状态色
  - **Vercel Dashboard**：克制的 luxury 暗色 + 锐利的字体
  - **Notion**（文档区）：版面节奏 + 引用呈现
  - **Hex / Retool**（数据工具）：dense 但不 cluttered

---

## Tone

- 文案：**事实陈述**，不用感叹号，不用"轻松搞定"这类轻佻表达。中文用书面体，英文用 sentence case。
- 状态信息：精确到秒、精确到 token、精确到 confidence。
- 错误信息：先说**发生了什么**，再说**用户可以做什么**，从不只说"出错了"。
- 不绝对化（"行业唯一"、"完美"等已在 QA Agent 禁用词列表，UI 也同步禁用）。

---

## Anti-References

强反对：

- **❌ 消费级 SaaS 的撞色** — 大面积品牌色 hero、卡通插画、emoji 装饰、"Get started in seconds!" 大字
- **❌ AI 产品千篇一律的紫色渐变** — Linear gradient purple、glassmorphism 卡片、亮闪闪图标
- **❌ Dashboard 套路** — hero metric 大数字 + 小标签 + gradient accent 三件套
- **❌ 等大卡片网格** — 一排 4 张同尺寸 card with icon + heading + text
- **❌ 模态对话框作为默认交互** — 详情应该在主区上下文里展开，不是 modal 弹层
- **❌ 装饰性侧边色条** — `border-left: 4px solid blue` 这种装饰
- **❌ 渐变文字** — `background-clip: text` 配 gradient
- **❌ 圆角拉满 + 阴影厚** — 那是 2020 年的 Notion 仿品

可以借鉴但要超越的：

- ⚠️ **过度照搬 Linear 的全黑** — 我们要 luxury，不是极客冷。可以更暖、更克制（参考 Vercel 的 zinc 系而非 Linear 的 pure black）
- ⚠️ **Stripe 的紫色** — 不抄它的紫，但抄它对状态色的克制使用

---

## Strategic Principles

按重要性排序的设计原则：

1. **证据先行**（Evidence-first）  
   每个事实性陈述附 evidence；UI 必须让"哪个结论 → 哪条证据 → 哪个原文"形成毫无歧义的视觉链路。这是平台 USP，必须在 UI 最显眼的位置体现。

2. **过程可见**（Process-transparent）  
   DAG 不是后台日志，是一等公民。用户能看到"5 个 Agent 是怎么协作出这份报告的"。决策回放是核心交互，不是埋在 settings 里的 debug 功能。

3. **信息密度优先**（Dense over decorative）  
   B 端工具的核心是单屏信息量。仿照 Linear / Hex，**dense but not cluttered**——靠精准的对齐、克制的色彩、清晰的层级让密度可读，不是靠堆装饰。

4. **可信任而非好看**（Trustworthy over impressive）  
   分析师工具的最高赞美是"可信"，不是"漂亮"。所以 UI 不应该让人觉得"AI 做的"——典型 AI slop 特征（紫渐变、玻璃态、emoji icon、过度圆角）一律禁止。

5. **状态明确**（Status-explicit）  
   节点、报告、Evidence、QA verdict 都有明确状态（running / success / needs_rework / disputed / stale）。用户任何时候都能看到"这玩意儿现在是什么状态、新不新鲜、可不可信"。

6. **可介入而非黑盒**（Editable, not opaque）  
   用户可以编辑段落、标记 Evidence 不准确、触发重审。所有人工介入都计入指标（人工修正率），不藏起来。

7. **键盘友好**（Keyboard-first）  
   B 端用户的核心交互速率指标。`⌘K` 全局命令面板、`j/k` 列表导航、`?` 显示快捷键。仿 Linear。

---

## Aesthetic Direction（已锁定 · 2026-05-29 修订）

- **审美轴**：Luxury（克制 / 专业 / 严肃工程美感）+ **清爽靓丽 + 不 AI 化**
- **关键反思**：dark + monochrome 在 2026 已经被 AI 圈占有（Anthropic / OpenAI / Linear / Vercel / Cursor），底色一旦是 dark 第一眼就还是"AI 工具"。所以 v1 改为 **light-first**。
- **主题**：**Light-first**，并已实现完整 dark mode（用户可手动 toggle，主推 light）。Scene：「PM 在自然光办公室、9–6 工作时段、经常投屏给老板/客户」——强制 light 主推 + 高饱和状态色 + 淡紫中性背景。
- **色彩策略**：Committed（不是 Restrained）——accent 在 UI 中占 15–20%，明显但不装饰。
- **Accent**：**紫罗兰** `oklch(58% 0.22 285)`——明亮 ToB 数据工具气质（Similarweb 风），在浅紫中性背景上清晰可辨。
- **完整 token 体系** 见 [DESIGN.md](DESIGN.md)。

---

## Surfaces

v1 必交付的前端 surface（按优先级）：

| # | Surface | 优先级 |
|---|---|---|
| 1 | DAG 实时监控页 | P0 |
| 2 | Report 查看 + 证据溯源页 | P0 |
| 3 | Agent 决策回放（节点详情抽屉） | P0 |
| 4 | Project 创建配置页 | P0 |
| 5 | 业务指标仪表盘 | P1 |
| 6 | Evidence 库浏览 | P1 |
| 7 | Project 列表 / 历史 | P2 |
| 8 | 全局命令面板（⌘K） | P2 |

后两个移动端不优化（只 graceful degrade 到可读）。
