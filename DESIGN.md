# DESIGN.md

> 设计系统的权威定义。前端 impeccable skill 每次启动时与 PRODUCT.md 一起加载。
> 任何 token 变更必须经过架构窗口确认。

---

## Theme · 锁定

**Light-first**，暂不实现 dark mode（v2 再考虑）。

**Scene sentence**（驱动决策的真实使用场景）：

> 产品经理在自然光充足的办公室、9–6 工作时段，使用 27 寸或 14 寸笔记本；
> 经常把界面**投屏到会议室大屏给老板或客户看**，需要白天不刺眼 + 投屏不发灰 + 截图发同事不奇怪。

这个句子强制：
- 浅色背景（暗底投屏发灰、刺眼、AI 工具感）
- 高饱和的状态色（白底要靠饱和度突出，不像暗底可以哑光）
- 暖色调中性色（不是 Apple 灰、不是 Linear 灰，避免冷感）

---

## Color Strategy · Committed

参考 impeccable shared laws，product register 默认 Restrained，**本项目升级为 Committed**：
accent 出现频率更高（按钮、当前选中、关键状态指示、章节锚点、链接），约占视觉 15–20%。

理由：
- 评分项「独特或前瞻性思考」需要色彩气质可读
- 答辩 demo 需要"一眼记住"
- B 端工具的高频操作（点击 / 选中）值得用 accent 强化反馈

但**装饰性使用仍禁止**——见 § Anti-patterns。

---

## Color Tokens（OKLCH，权威）

所有 token 注册在 Tailwind config 的 `:root` CSS variables 中。**禁止在组件内写魔法色值**。

### Background

```
--bg-base       oklch(98.5% 0.005 80)   暖奶白，主背景
--bg-raised     oklch(100% 0 0)         纯白，卡片 / 数据行
--bg-sunken     oklch(96% 0.006 80)     微沉，sidebar / 信息次级区
--bg-overlay    oklch(99% 0.004 80)     抽屉 / popover（与 raised 几乎平，仅靠 border 分层）
--bg-hover      oklch(94% 0.008 60)     hover 极浅暖底（不是蓝色高亮！）
--bg-selected   oklch(92% 0.04 40)      选中态，朱漆橙的 4% chroma 痕迹
```

### Border

```
--border-subtle   oklch(93% 0.008 80)   分隔线 / 卡片边
--border-default  oklch(88% 0.01 80)    input / 弹层边
--border-strong   oklch(82% 0.015 80)   active input / 强调分组
--border-focus    oklch(64% 0.20 38)    focus ring = accent
```

### Text

```
--text-primary    oklch(18% 0.012 60)   近黑但暖，正文
--text-secondary  oklch(38% 0.012 65)   次要信息
--text-muted      oklch(58% 0.01 70)    标签 / 时间戳 / 占位
--text-inverse    oklch(99% 0.005 80)   只在 accent / state 背景上的反色
--text-accent     oklch(54% 0.22 38)    可点击的强调字（链接、当前选中）
```

### Accent · 朱漆橙

```
--accent-base    oklch(64% 0.20 38)    主 accent，按钮 / 链接 / 当前选中
--accent-hover   oklch(58% 0.22 36)    hover 加深
--accent-active  oklch(52% 0.23 35)    按下
--accent-bg      oklch(96% 0.04 40)    accent 浅底（selected / chip 背景）
--accent-border  oklch(76% 0.16 42)    accent 边框（accent 浅底配套）
```

### Semantic States

每个状态有 `base`（实色）/ `bg`（浅底）/ `border`（浅底配套）三层。

```
--success-base    oklch(58% 0.16 150)   竹绿，已完成 / verified
--success-bg      oklch(96% 0.035 150)
--success-border  oklch(78% 0.13 150)

--warning-base    oklch(70% 0.17 85)    柿黄，需注意 / stale evidence
--warning-bg      oklch(97% 0.04 85)
--warning-border  oklch(82% 0.14 85)

--error-base      oklch(56% 0.21 25)    朱砂红，失败 / disputed
--error-bg        oklch(96% 0.04 25)
--error-border    oklch(78% 0.15 25)

--running-base    oklch(62% 0.16 220)   湖蓝，进行中（带 pulse）
--running-bg      oklch(96% 0.03 220)
--running-border  oklch(78% 0.13 220)

--rework-base     oklch(64% 0.18 50)    深橙（不撞 accent 的纯橙），QA 反馈 routing
--rework-bg       oklch(96% 0.035 50)
--rework-border   oklch(80% 0.14 50)

--neutral-base    oklch(54% 0.01 70)    pending / 未启动
--neutral-bg      oklch(94% 0.005 70)
--neutral-border  oklch(86% 0.008 70)
```

### Data Visualization

ECharts 系列色（最多 6 个产品对比，按出现顺序使用）：

```
--viz-1   oklch(64% 0.20 38)    朱漆橙（=accent，target 产品）
--viz-2   oklch(58% 0.16 215)   海石青
--viz-3   oklch(58% 0.16 150)   竹绿
--viz-4   oklch(64% 0.18 295)   葡萄紫（位置很后，安全使用）
--viz-5   oklch(70% 0.17 85)    柿黄
--viz-6   oklch(54% 0.01 70)    中性灰
```

**规则**：target_product 永远 viz-1（朱漆橙），让"主视角"在所有图里一致。

---

## Typography

### Family

```
font-sans   "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC",
            "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif
font-mono   "JetBrains Mono", "SF Mono", "Consolas", ui-monospace, monospace
```

中英混排时 Inter 兜底，中文交给 PingFang SC（Apple）/ Microsoft YaHei（Windows）。

**不引入 display / serif 字体**——产品工具不需要。

### Scale（rem，固定不流体）

| Token | rem / px | line-height | weight | 用途 |
|---|---|---|---|---|
| `text-xs` | 0.75 / 12 | 1.4 | 500 | caption / chip / 时间戳 |
| `text-sm` | 0.8125 / 13 | 1.5 | 400 | data row / 抽屉次要标签 |
| `text-base` | 0.875 / 14 | 1.55 | 400 | **产品 UI body 默认** |
| `text-md` | 1 / 16 | 1.6 | 400 | 报告正文 / form input |
| `text-lg` | 1.125 / 18 | 1.4 | 600 | section heading |
| `text-xl` | 1.375 / 22 | 1.3 | 600 | page title |
| `text-2xl` | 1.75 / 28 | 1.2 | 700 | 极罕用（项目名等少数场景） |

### Weight

`400` body / `500` label & emphasis / `600` heading / `700` 罕用顶级标题。**禁止 800/900**。

### Numerals

数字优先 `font-variant-numeric: tabular-nums`——所有指标、价格、token 数对齐。

---

## Spacing · 4px grid

```
0 · 2 · 4 · 6 · 8 · 10 · 12 · 16 · 20 · 24 · 32 · 40 · 56 · 80
```

**不用 8px grid**——对 dense product UI 太粗。

### 常用映射

| 用途 | px |
|---|---|
| 紧邻 inline 元素间距 | 4 / 6 |
| 同组内行距 | 8 / 10 |
| 卡片内 padding | 16 / 20 |
| 卡片之间 | 12 / 16 |
| section 之间 | 24 / 32 |
| page hero 上下 padding | 40 / 56 |
| 大区块极限 | 80 |

---

## Radius

```
--radius-sm   4px    input / chip / button
--radius-md   6px    card / drawer item
--radius-lg   8px    drawer / modal / 大卡片
--radius-pill 999px  badge / tag
```

**不超过 8px**。圆角拉太大是 2020 Notion 仿品感。

---

## Shadow

几乎不用 `box-shadow`。层级靠 `border` + `background` 差异。

唯一例外：

```
--shadow-drawer   0 0 0 1px oklch(82% 0.015 80 / 0.5),
                  -8px 0 24px -8px oklch(60% 0.05 80 / 0.08)
--shadow-popover  0 0 0 1px oklch(82% 0.015 80 / 0.5),
                  0 4px 12px -2px oklch(60% 0.05 80 / 0.08)
```

注意阴影**带 hue tint（80 偏暖）**，不要冷灰阴影。

---

## Motion

### Durations

| 场景 | ms |
|---|---|
| hover 色变 / focus ring | 120 |
| tab 切换 (crossfade) | 180 |
| 抽屉滑入 / 模态显隐 | 240 |
| 节点状态变化（色 + scale） | 200 |
| running pulse 周期 | 1500 |

**禁止超过 320ms**——B 端用户在 task flow，没耐心等动画。

### Easing

**唯一允许的曲线**：`cubic-bezier(0.25, 1, 0.5, 1)` (ease-out-quart)。

变量名：`--ease-out`。

**禁止**：spring / bounce / elastic / cubic-bezier(0, 0, 1, 1) 直线、`ease-in-out`（除 pulse 循环）、orchestrated 入场序列。

### Properties

- ✅ 可动画：`background-color` / `border-color` / `color` / `opacity` / `transform` / `scale`
- ❌ 禁止动画：`width` / `height` / `margin` / `padding` / `top` / `left`（用 `transform` 代替）

---

## Components

### Primitives（shadcn/ui 基础）

| 组件 | 规则 |
|---|---|
| `Button` | 三种 variant：`primary` (accent fill) / `secondary` (border-default) / `ghost` (transparent + hover-bg)。**唯一可用 size**：`sm` 32px / `md` 36px / `lg` 40px。不允许 xs (28px) 或 xl (44px+) |
| `Input` | 36px 高，border-default → focus border-focus + accent shadow ring 0 0 0 3px oklch(64% 0.20 38 / 0.15) |
| `Card` | bg-raised + border-subtle，padding 16 / 20。**禁止嵌套 Card**，禁止侧边色条 |
| `Badge` | radius-pill，padding 2px 8px，text-xs 500，按状态用对应的 `*-bg` + `*-border` + `*-base` |
| `Drawer` | 360–480px 宽，从右侧滑入，shadow-drawer，禁止从底部滑（移动端模式） |
| `Modal` | **尽量避免**。需要时居中，max-w 560px，border-default + shadow-popover |
| `Tabs` | underline style，active tab border-bottom 2px accent-base，**禁止 pill / segmented control** |
| `Tooltip` | bg-overlay + border-default + shadow-popover，最多两行 |

### Status Badge 映射

```
status        bg              border          text
─────────────────────────────────────────────────────
pending       neutral-bg      neutral-border  neutral-base
running       running-bg      running-border  running-base   + pulse
success       success-bg      success-border  success-base
needs_rework  rework-bg       rework-border   rework-base
failed        error-bg        error-border    error-base
skipped       neutral-bg      neutral-border  text-muted
verified      success-bg      success-border  success-base   （Evidence）
disputed      error-bg        error-border    error-base    （Evidence）
stale         warning-bg      warning-border  warning-base  （Evidence）
```

### State Vocabulary

每个交互组件**必须**实现：

```
default · hover · focus · active · disabled · loading · error
```

ship 前没全实现的组件视为半成品。

---

## DAG 节点视觉

React Flow 节点是项目最关键的视觉对象，单独规范：

```
默认尺寸  156 × 72 px  (rounded-md = 6px)
内容布局  
   ┌─────────────────────────┐
   │ ●status   agent_name    │  ← text-sm 500
   │ collect:notion          │  ← text-xs muted
   │ 4.2s · 1.2k tokens      │  ← text-xs tabular-nums muted
   └─────────────────────────┘

颜色映射：
   bg-raised + border 用 status 对应的 *-border
   左 ●（圆点 8px）填 status 对应的 *-base
   running 时 ● 做 pulse 透明度 1500ms 循环

selected: border-focus + shadow-popover

边 (依赖)  oklch(82% 0.015 80) 1.5px 直线
边 (反馈)  rework-border 1.5px 虚线 6 4，hover 整条高亮 rework-base
```

---

## Anti-patterns（项目级硬禁）

shared laws 已禁的（侧边色条 / 渐变文字 / glassmorphism / hero-metric 模板 / 等大卡片网格 / 模态优先），叠加项目级补充：

| ❌ | ✅ 替代 |
|---|---|
| 大面积紫色 / 蓝色渐变 | 单一朱漆橙 + 中性暖背景 |
| 卡通 / 3D / emoji 图标 | Lucide line icon，1.5px stroke，统一 16/18/20 三档尺寸 |
| Hero `<h1>` 60px+ | 最大 28px (text-2xl)，且仅 1 处 |
| 「100+」「无限」「极速」营销文案 | 精确数字 + 单位（已与 QA 禁用词同步） |
| ↗ 箭头 + bouncing 提示 | 静态 icon，hover 微 0.05 opacity 变化 |
| Skeleton + spinner 双用 | 只用 skeleton（spinner 仅按钮内 loading） |

---

## Tailwind Config 接入要点

```ts
// tailwind.config.ts (片段)
{
  theme: {
    extend: {
      colors: {
        bg: {
          base:    'var(--bg-base)',
          raised:  'var(--bg-raised)',
          sunken:  'var(--bg-sunken)',
          overlay: 'var(--bg-overlay)',
          hover:   'var(--bg-hover)',
          selected:'var(--bg-selected)',
        },
        border: { /* subtle/default/strong/focus */ },
        text:   { /* primary/secondary/muted/inverse/accent */ },
        accent: { /* base/hover/active/bg/border */ },
        success: { /* base/bg/border */ },
        warning: { /* ... */ },
        error:   { /* ... */ },
        running: { /* ... */ },
        rework:  { /* ... */ },
        neutral: { /* ... */ },
        viz: { 1: '...', 2: '...', ..., 6: '...' },
      },
      borderRadius: { sm: '4px', md: '6px', lg: '8px', pill: '9999px' },
      transitionTimingFunction: { 'out-quart': 'cubic-bezier(0.25, 1, 0.5, 1)' },
      transitionDuration: { 120: '120ms', 180: '180ms', 240: '240ms' },
      fontFamily: { sans: ['var(--font-sans)'], mono: ['var(--font-mono)'] },
      boxShadow: { drawer: 'var(--shadow-drawer)', popover: 'var(--shadow-popover)' },
      spacing: {
        // 4px grid
        '0.5': '2px', '1': '4px', '1.5': '6px', '2': '8px',
        '2.5': '10px', '3': '12px', '4': '16px', '5': '20px',
        '6': '24px', '8': '32px', '10': '40px', '14': '56px', '20': '80px',
      },
    },
  },
}
```

实际落到 `frontend/src/styles/tokens.css` (CSS variables) + `frontend/tailwind.config.ts`（Tailwind 暴露）。

---

## 验收标准

任何前端 PR 在合并前自检：

- [ ] 没有出现 `#` 开头的硬编码颜色（除 Tailwind class）
- [ ] 没有 inline style 的 color/margin/padding
- [ ] 所有按钮 ≤ 3 种 variant（primary / secondary / ghost）
- [ ] 所有阴影来自 token，没自定义
- [ ] focus ring 可见且使用 accent
- [ ] hover/focus/active/disabled 至少有 3 个状态
- [ ] 暗黑模式相关代码**不存在**（v1 不做）
- [ ] 单文件 className 不超过 8 个 Tailwind class（超出抽 component）
- [ ] 中文段落字号 ≥ 14px

---

## 后续

DESIGN.md = **设计契约**，类比 SCHEMA.md = **数据契约**。
任一 token 变更走 PR + 架构窗口审查，与 schema 变更同等级别。

下一步：以本文件为基线 shape 第一个具体页面（推荐 Workspace · DAG tab）。
