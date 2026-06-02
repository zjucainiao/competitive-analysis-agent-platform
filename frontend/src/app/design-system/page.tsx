import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Design System Showcase
 *
 * 不是生产页面。用途：
 * 1) 验证 OKLCH token → shadcn 组件正确生效
 * 2) 给后续 shape / craft 调用一份"真实的 token 在跑"的视觉参考
 * 3) 评审 PR 时一键打开看每个 token 当前长什么样
 *
 * 详细规范见 /DESIGN.md。
 */
export default function ShowcasePage() {
  return (
    <div className="min-h-full bg-background">
      <header className="border-b border-border-subtle px-10 py-10">
        <div className="mx-auto max-w-6xl">
          <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
            Design System · v1.0
          </div>
          <h1 className="mt-3 text-[28px] font-semibold leading-tight tracking-tight text-text-primary">
            Atlas · 竞品分析 Agent 协作平台
          </h1>
          <p className="mt-2 max-w-xl text-base text-text-secondary">
            朱漆橙 accent · 暖奶白底 · light-first · committed color strategy。
            这一页用来核对 token 系统是否完整生效。
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-10 py-16 space-y-20">
        <SectionTypography />
        <Separator />
        <SectionSurface />
        <Separator />
        <SectionStateColors />
        <Separator />
        <SectionDataViz />
        <Separator />
        <SectionPrimitives />
        <Separator />
        <SectionDagNodes />
        <Separator />
        <SectionComposition />
      </main>

      <footer className="border-t border-border-subtle px-10 py-8">
        <div className="mx-auto max-w-6xl text-xs text-text-muted">
          tokens authority · <code className="font-mono">/DESIGN.md</code>
          {"   ·   "}
          product context · <code className="font-mono">/PRODUCT.md</code>
        </div>
      </footer>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function SectionHeading({
  number,
  title,
  hint,
}: {
  number: string;
  title: string;
  hint?: string;
}) {
  return (
    <div className="mb-8">
      <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
        {number}
      </div>
      <h2 className="mt-1.5 text-lg font-semibold text-text-primary">{title}</h2>
      {hint ? (
        <p className="mt-1 text-sm text-text-secondary">{hint}</p>
      ) : null}
    </div>
  );
}

/* ── 1. Typography ───────────────────────────────────────────────────────── */

function SectionTypography() {
  return (
    <section>
      <SectionHeading
        number="01"
        title="Typography"
        hint="Inter (sans) + JetBrains Mono (mono) · 中文 PingFang SC 兜底 · 1.125–1.2 阶梯"
      />
      <div className="space-y-5">
        <Row label="text-2xl / 28px / 700">
          <p className="text-2xl font-bold text-text-primary">
            竞品分析 Agent 协作平台
          </p>
        </Row>
        <Row label="text-xl / 22px / 600">
          <p className="text-xl font-semibold text-text-primary">
            DAG 任务流转可视化
          </p>
        </Row>
        <Row label="text-lg / 18px / 600">
          <p className="text-lg font-semibold text-text-primary">
            核心功能对比
          </p>
        </Row>
        <Row label="text-md / 16px / 400">
          <p className="text-base text-text-primary">
            Notion 以文档+数据库灵活组合见长，AI 能力内嵌于编辑器，
            适合知识密集型工作流。
          </p>
        </Row>
        <Row label="text-base / 14px / 400 · UI 默认">
          <p className="text-sm text-text-primary">
            点击节点 → 右侧抽屉显示完整 trace（prompt / input / output / token）
          </p>
        </Row>
        <Row label="text-sm / 13px / 400 · 数据行">
          <p className="text-[13px] text-text-secondary">
            collect:notion · success · 4.2s · 1,234 tokens
          </p>
        </Row>
        <Row label="text-xs / 12px / 500 · chip / 时间戳">
          <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
            collected 2 days ago
          </p>
        </Row>
        <Row label="font-mono · tabular-nums · IDs / 数字">
          <p className="font-mono text-sm text-text-secondary" data-num>
            ev_a8f2 · trace_id abc123 · $0.018 · 1,234/421 tokens
          </p>
        </Row>
      </div>
    </section>
  );
}

/* ── 2. Surface tokens ───────────────────────────────────────────────────── */

function SectionSurface() {
  const swatches: Array<{ name: string; cls: string; label: string }> = [
    { name: "bg-base", cls: "bg-background", label: "主背景" },
    { name: "bg-raised", cls: "bg-bg-raised", label: "卡片 · 数据行" },
    { name: "bg-sunken", cls: "bg-bg-sunken", label: "sidebar · 次级区" },
    { name: "bg-overlay", cls: "bg-bg-overlay", label: "抽屉 · popover" },
    { name: "bg-hover", cls: "bg-bg-hover", label: "hover 态" },
    { name: "bg-selected", cls: "bg-bg-selected", label: "选中态" },
  ];

  return (
    <section>
      <SectionHeading
        number="02"
        title="Surface · Border · Text"
        hint="六个 surface 层级 + 四个 border 层级 + 五个 text 角色"
      />
      <div className="grid grid-cols-3 gap-4">
        {swatches.map((s) => (
          <div
            key={s.name}
            className={cn(
              "rounded-md border border-border-subtle p-4",
              s.cls
            )}
          >
            <div className="font-mono text-xs text-text-muted">{s.name}</div>
            <div className="mt-2 text-sm text-text-primary">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-4 gap-4">
        <BorderSwatch name="border-subtle" cls="border-border-subtle" />
        <BorderSwatch name="border-default" cls="border-border-default" />
        <BorderSwatch name="border-strong" cls="border-border-strong" />
        <BorderSwatch
          name="border-focus (= accent)"
          cls="border-border-focus"
        />
      </div>

      <div className="mt-6 rounded-md bg-bg-raised border border-border-subtle p-5 space-y-2">
        <p className="text-base text-text-primary">
          text-primary · oklch(18% 0.012 60)
        </p>
        <p className="text-base text-text-secondary">
          text-secondary · oklch(38% 0.012 65)
        </p>
        <p className="text-base text-text-muted">
          text-muted · oklch(58% 0.01 70)
        </p>
        <p className="text-base font-medium text-text-accent">
          text-accent · 可点击的强调字
        </p>
        <div className="mt-3 inline-block rounded-md bg-accent-base px-3 py-1">
          <span className="text-sm text-text-inverse">
            text-inverse · 仅在 accent / state base 背景上使用
          </span>
        </div>
      </div>
    </section>
  );
}

function BorderSwatch({ name, cls }: { name: string; cls: string }) {
  return (
    <div
      className={cn(
        "rounded-md bg-bg-raised p-4 border-2",
        cls
      )}
    >
      <div className="font-mono text-xs text-text-muted">{name}</div>
    </div>
  );
}

/* ── 3. State colors ─────────────────────────────────────────────────────── */

const STATES = [
  { key: "success", label: "Success · verified", base: "bg-success-base", bg: "bg-success-bg", border: "border-success-border", text: "text-success-base" },
  { key: "running", label: "Running · in-progress", base: "bg-running-base", bg: "bg-running-bg", border: "border-running-border", text: "text-running-base", pulse: true },
  { key: "rework", label: "Needs rework · QA route-back", base: "bg-rework-base", bg: "bg-rework-bg", border: "border-rework-border", text: "text-rework-base" },
  { key: "warning", label: "Warning · stale evidence", base: "bg-warning-base", bg: "bg-warning-bg", border: "border-warning-border", text: "text-warning-base" },
  { key: "error", label: "Error · failed", base: "bg-error-base", bg: "bg-error-bg", border: "border-error-border", text: "text-error-base" },
  { key: "neutral", label: "Neutral · pending", base: "bg-neutral-base", bg: "bg-neutral-bg", border: "border-neutral-border", text: "text-neutral-base" },
];

function SectionStateColors() {
  return (
    <section>
      <SectionHeading
        number="03"
        title="Semantic states · 6 角色 × 3 层"
        hint="每个状态有 base (实色) / bg (浅底) / border (浅底边) 三层 · running 自带 pulse"
      />
      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        {STATES.map((s) => (
          <div
            key={s.key}
            className={cn(
              "flex items-center gap-3 rounded-md border px-4 py-3",
              s.bg,
              s.border
            )}
          >
            <span
              className={cn(
                "h-2.5 w-2.5 rounded-pill shrink-0",
                s.base,
                s.pulse && "animate-pulse-soft"
              )}
            />
            <span className="font-mono text-xs text-text-muted shrink-0">
              {s.key}
            </span>
            <span className={cn("text-sm font-medium", s.text)}>
              {s.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── 4. Data Viz palette ─────────────────────────────────────────────────── */

const VIZ = [
  { name: "viz-1", label: "朱漆橙 · target", cls: "bg-viz-1" },
  { name: "viz-2", label: "海石青", cls: "bg-viz-2" },
  { name: "viz-3", label: "竹绿", cls: "bg-viz-3" },
  { name: "viz-4", label: "葡萄紫", cls: "bg-viz-4" },
  { name: "viz-5", label: "柿黄", cls: "bg-viz-5" },
  { name: "viz-6", label: "中性灰", cls: "bg-viz-6" },
];

function SectionDataViz() {
  return (
    <section>
      <SectionHeading
        number="04"
        title="Data viz palette · ECharts"
        hint="target_product 永远 viz-1（朱漆橙），让主视角在所有图里一致"
      />
      <div className="flex gap-2">
        {VIZ.map((v) => (
          <div key={v.name} className="flex-1">
            <div className={cn("h-16 rounded-md", v.cls)} />
            <div className="mt-2 font-mono text-xs text-text-muted">
              {v.name}
            </div>
            <div className="text-xs text-text-secondary">{v.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── 5. Primitives ───────────────────────────────────────────────────────── */

function SectionPrimitives() {
  return (
    <section>
      <SectionHeading
        number="05"
        title="Primitives"
        hint="shadcn (base-ui/react) 组件 · 已对接朱漆橙"
      />

      <div className="space-y-8">
        <div>
          <Label>Button · 3 variants × 3 sizes</Label>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button>Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="destructive">Destructive</Button>
            <Button disabled>Disabled</Button>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button size="sm">sm</Button>
            <Button size="default">md</Button>
            <Button size="lg">lg</Button>
          </div>
        </div>

        <div>
          <Label>Badge · 状态映射（含 Evidence freshness）</Label>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge>Default</Badge>
            <Badge variant="secondary">Secondary</Badge>
            <Badge variant="outline">Outline</Badge>
            <Badge variant="destructive">Destructive</Badge>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <StatusBadge tone="success" label="verified" />
            <StatusBadge tone="running" label="running" pulse />
            <StatusBadge tone="rework" label="needs-rework" />
            <StatusBadge tone="warning" label="stale" />
            <StatusBadge tone="error" label="disputed" />
            <StatusBadge tone="neutral" label="pending" />
          </div>
        </div>

        <div>
          <Label>Input · focus ring 使用 accent</Label>
          <div className="mt-3 grid grid-cols-2 gap-3 max-w-xl">
            <Input placeholder="目标产品（如 Notion）" />
            <Input placeholder="禁用态" disabled />
          </div>
        </div>

        <div>
          <Label>Tooltip · base-ui 实现 · delay 150ms</Label>
          <div className="mt-3 flex gap-3">
            <Tooltip>
              <TooltipTrigger
                render={<Button variant="outline">悬停看 trace</Button>}
              />
              <TooltipContent>
                <span className="font-mono">trace abc123 · span_xyz</span>
              </TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                render={<Button variant="ghost">show details</Button>}
              />
              <TooltipContent side="right">
                4.2s · 1,234 / 421 tokens · $0.018
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
      </div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
      {children}
    </div>
  );
}

function StatusBadge({
  tone,
  label,
  pulse,
}: {
  tone: "success" | "running" | "rework" | "warning" | "error" | "neutral";
  label: string;
  pulse?: boolean;
}) {
  const map: Record<string, { bg: string; border: string; text: string; dot: string }> = {
    success: {
      bg: "bg-success-bg",
      border: "border-success-border",
      text: "text-success-base",
      dot: "bg-success-base",
    },
    running: {
      bg: "bg-running-bg",
      border: "border-running-border",
      text: "text-running-base",
      dot: "bg-running-base",
    },
    rework: {
      bg: "bg-rework-bg",
      border: "border-rework-border",
      text: "text-rework-base",
      dot: "bg-rework-base",
    },
    warning: {
      bg: "bg-warning-bg",
      border: "border-warning-border",
      text: "text-warning-base",
      dot: "bg-warning-base",
    },
    error: {
      bg: "bg-error-bg",
      border: "border-error-border",
      text: "text-error-base",
      dot: "bg-error-base",
    },
    neutral: {
      bg: "bg-neutral-bg",
      border: "border-neutral-border",
      text: "text-neutral-base",
      dot: "bg-neutral-base",
    },
  };
  const t = map[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-xs font-medium",
        t.bg,
        t.border,
        t.text
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-pill",
          t.dot,
          pulse && "animate-pulse-soft"
        )}
      />
      {label}
    </span>
  );
}

/* ── 6. DAG node atom（项目标志性视觉） ──────────────────────────────────── */

function SectionDagNodes() {
  return (
    <section>
      <SectionHeading
        number="06"
        title="DAG node atom"
        hint="React Flow 节点的视觉标准 · 156×72 · 状态色驱动 border + 圆点"
      />
      <div className="rounded-lg border border-border-subtle bg-bg-sunken p-8">
        <div className="flex flex-wrap gap-4">
          <DagNode status="success" agent="collect" target="notion" duration="4.2s" tokens="1.2k" />
          <DagNode status="running" agent="collect" target="clickup" duration="2.1s" tokens="—" />
          <DagNode status="neutral" agent="collect" target="asana" duration="" tokens="" />
          <DagNode status="success" agent="extract" target="notion" duration="12.4s" tokens="4.8k" />
          <DagNode status="rework" agent="reporter" target="" duration="18.6s" tokens="8.4k" />
          <DagNode status="error" agent="qa" target="" duration="8.2s" tokens="7.8k" />
        </div>
      </div>
    </section>
  );
}

function DagNode({
  status,
  agent,
  target,
  duration,
  tokens,
}: {
  status: "success" | "running" | "rework" | "error" | "neutral";
  agent: string;
  target?: string;
  duration: string;
  tokens: string;
}) {
  const map: Record<string, { dot: string; border: string; pulse?: boolean }> = {
    success: { dot: "bg-success-base", border: "border-success-border" },
    running: { dot: "bg-running-base", border: "border-running-border", pulse: true },
    rework: { dot: "bg-rework-base", border: "border-rework-border" },
    error: { dot: "bg-error-base", border: "border-error-border" },
    neutral: { dot: "bg-neutral-base", border: "border-neutral-border" },
  };
  const t = map[status];
  const name = target ? `${agent}:${target}` : agent;
  const ready = duration || tokens;
  return (
    <div
      className={cn(
        "w-[156px] rounded-md border bg-bg-raised px-3 py-2.5",
        t.border
      )}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "h-2 w-2 rounded-pill shrink-0",
            t.dot,
            t.pulse && "animate-pulse-soft"
          )}
        />
        <span className="text-sm font-medium text-text-primary truncate">
          {name}
        </span>
      </div>
      <div className="mt-1.5 text-[11px] font-mono text-text-muted tabular-nums">
        {ready ? (
          <>
            {duration || "—"} · {tokens || "—"} tokens
          </>
        ) : (
          <span className="italic">pending</span>
        )}
      </div>
    </div>
  );
}

/* ── 7. Composition example ──────────────────────────────────────────────── */

function SectionComposition() {
  return (
    <section>
      <SectionHeading
        number="07"
        title="Composition · Card + Tabs"
        hint="组合演示 · 一个 mini run summary 卡片"
      />
      <Card className="max-w-3xl">
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="text-lg">
                协作办公 SaaS Demo · run #03
              </CardTitle>
              <CardDescription>
                Notion · ClickUp · Asana · template:{" "}
                <code className="font-mono">standard_v1</code>
              </CardDescription>
            </div>
            <StatusBadge tone="running" label="running 2m 48s" pulse />
          </div>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="dag">
            <TabsList>
              <TabsTrigger value="dag">DAG</TabsTrigger>
              <TabsTrigger value="report">Report</TabsTrigger>
              <TabsTrigger value="trace">Trace</TabsTrigger>
              <TabsTrigger value="evidence">Evidence</TabsTrigger>
              <TabsTrigger value="metrics">Metrics</TabsTrigger>
            </TabsList>
            <TabsContent value="dag" className="mt-4">
              <Stat label="节点">12 / 14</Stat>
              <Stat label="并行度">3</Stat>
              <Stat label="重做">1（reporter → qa）</Stat>
            </TabsContent>
            <TabsContent value="report" className="mt-4">
              <p className="text-sm text-text-secondary">
                v1 草稿生成中 · 4 章 · 已绑定 evidence 10 条
              </p>
            </TabsContent>
            <TabsContent value="trace" className="mt-4">
              <p className="text-sm text-text-secondary font-mono" data-num>
                12 spans · 28,432 tokens · $0.31 · 4m 18s
              </p>
            </TabsContent>
            <TabsContent value="evidence" className="mt-4">
              <p className="text-sm text-text-secondary">
                Evidence 库：48 条 · 来源 11 个 · 平均 authority 0.91
              </p>
            </TabsContent>
            <TabsContent value="metrics" className="mt-4">
              <Stat label="accuracy">0.94</Stat>
              <Stat label="coverage">0.81</Stat>
              <Stat label="edit rate">0.15</Stat>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </section>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between py-2 border-b border-border-subtle last:border-b-0">
      <span className="text-xs uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span className="font-mono text-sm text-text-primary tabular-nums">
        {children}
      </span>
    </div>
  );
}

/* ── helper ──────────────────────────────────────────────────────────────── */

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[200px_1fr] gap-6 items-baseline">
      <div className="font-mono text-xs text-text-muted">{label}</div>
      <div>{children}</div>
    </div>
  );
}
