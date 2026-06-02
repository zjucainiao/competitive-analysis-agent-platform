"use client";

import { useState, useId } from "react";
import { useRouter } from "next/navigation";
import {
  CheckIcon,
  ArrowRightIcon,
  ArrowLeftIcon,
  XIcon,
  SparklesIcon,
  GlobeIcon,
  BuildingIcon,
  ShoppingCartIcon,
  GraduationCapIcon,
  WandIcon,
  ServerCogIcon,
  PlugZapIcon,
  Loader2Icon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { emitIntervention } from "@/lib/workspace-actions";
import { createProject, startRun, ApiError } from "@/lib/api/client";
import { revalidate } from "@/lib/api/hooks";
import type { AnalysisDimension } from "@/lib/api/types";

/* ── option definitions ──────────────────────────────────────────────── */

const INDUSTRIES = [
  {
    id: "collaboration_saas",
    label: "协作办公 SaaS",
    examples: "Notion · ClickUp · Asana · Lark",
    icon: GlobeIcon,
    note: "task / docs / kanban / automation",
    available: true,
  },
  {
    id: "crm_saas",
    label: "CRM SaaS",
    examples: "Salesforce · HubSpot · Pipedrive",
    icon: BuildingIcon,
    note: "lead / pipeline / customer lifecycle",
    available: true,
  },
  {
    id: "cross_border_ecommerce_saas",
    label: "跨境电商 SaaS",
    examples: "Shopify · BigCommerce · Wix Stores",
    icon: ShoppingCartIcon,
    note: "store / payment / logistics / multi-language",
    available: true,
  },
  {
    id: "edu_saas",
    label: "教育 SaaS",
    examples: "Canvas · Blackboard · 钉钉教育 · 学习通",
    icon: GraduationCapIcon,
    note: "course / assessment / classroom / analytics",
    available: true,
  },
];

const DIMENSIONS = [
  { id: "feature_comparison", label: "Features", hint: "功能对比矩阵" },
  { id: "pricing_comparison", label: "Pricing", hint: "定价档与溢价" },
  { id: "user_feedback", label: "User feedback", hint: "G2 / Capterra 评价洞察" },
  { id: "swot", label: "SWOT", hint: "围绕 target 视角" },
  { id: "differentiation_opportunities", label: "Differentiation", hint: "差异化机会" },
  { id: "positioning", label: "Positioning", hint: "定位 / 目标用户重叠" },
];

const MODES = [
  {
    id: "mock",
    label: "Mock",
    icon: WandIcon,
    note: "全链路用 fixture · 演示 / 单测",
    badge: "0 cost",
    badgeTone: "neutral" as const,
  },
  {
    id: "hybrid",
    label: "Hybrid",
    icon: PlugZapIcon,
    note: "真实采集 + Mock 兜底 · demo 推荐",
    badge: "low cost",
    badgeTone: "success" as const,
  },
  {
    id: "real",
    label: "Real",
    icon: ServerCogIcon,
    note: "全真 LLM + 真采集 · 生产 / 客户场景",
    badge: "burns key",
    badgeTone: "warning" as const,
  },
];

const STEPS = [
  { id: 1, label: "Target & competitors" },
  { id: 2, label: "Industry" },
  { id: 3, label: "Dimensions" },
  { id: 4, label: "Mode & review" },
];

const SAMPLE_TARGETS = ["Notion", "Linear", "Coda", "Figma"];
const SAMPLE_COMPETITORS: Record<string, string[]> = {
  Notion: ["ClickUp", "Asana", "Coda"],
  Linear: ["Jira", "Shortcut", "Height"],
  Coda: ["Notion", "Airtable"],
  Figma: ["Sketch", "Adobe XD", "Penpot"],
};

/* ── main ────────────────────────────────────────────────────────────── */

export function WizardLayout() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [target, setTarget] = useState("");
  const [competitors, setCompetitors] = useState<string[]>([]);
  const [industry, setIndustry] = useState<string | null>("collaboration_saas");
  const [dimensions, setDimensions] = useState<Set<string>>(
    new Set([
      "feature_comparison",
      "pricing_comparison",
      "swot",
      "differentiation_opportunities",
    ])
  );
  /* 后端 API 层只接受 "real"；UI 给三档可选但暂时只允许 real 提交 */
  const [mode, setMode] = useState<string>("real");
  const [submitting, setSubmitting] = useState(false);

  const canStep1Next = target.trim().length > 0 && competitors.length >= 1;
  const canStep2Next = industry !== null;
  const canStep3Next = dimensions.size >= 1;
  const canSubmit =
    canStep1Next && canStep2Next && canStep3Next && mode === "real";

  const next = () => setStep((s) => Math.min(STEPS.length, s + 1));
  const back = () => setStep((s) => Math.max(1, s - 1));

  const handleSubmit = async () => {
    if (submitting) return;
    setSubmitting(true);
    const payload = {
      project_name: `${target} vs ${competitors.join(" / ")}`,
      owner: "demo-user",
      target_product: target,
      competitors,
      industry: industry!,
      analysis_dimensions: Array.from(dimensions) as AnalysisDimension[],
      report_template_id: "standard_v1",
      mode: "real" as const,
    };
    emitIntervention("create-project", target);
    try {
      const project = await createProject(payload);
      toast.success("Project 已创建", {
        description: `${project.project_id} · 启动 run…`,
      });
      try {
        await startRun(project.project_id);
        toast.success("Run 已派发", {
          description: "实时进度通过 WebSocket 推送",
        });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          toast.warning("Run 已在运行，跳到 workspace");
        } else {
          toast.error("启动 Run 失败", {
            description: e instanceof Error ? e.message : String(e),
          });
        }
      }
      void revalidate.projects();
      router.push(`/projects/${project.project_id}/runs/${project.project_id}?tab=dag`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("创建项目失败", { description: msg });
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <Header step={step} />
      <Stepper step={step} setStep={setStep} />

      <div className="rounded-lg border border-border-subtle bg-bg-raised p-6">
        {step === 1 && (
          <Step1Target
            target={target}
            setTarget={setTarget}
            competitors={competitors}
            setCompetitors={setCompetitors}
          />
        )}
        {step === 2 && <Step2Industry value={industry} onChange={setIndustry} />}
        {step === 3 && (
          <Step3Dimensions value={dimensions} onChange={setDimensions} />
        )}
        {step === 4 && (
          <Step4Mode
            mode={mode}
            onChangeMode={setMode}
            target={target}
            competitors={competitors}
            industry={industry}
            dimensions={Array.from(dimensions)}
          />
        )}
      </div>

      <footer className="flex items-center justify-between gap-3">
        <Button variant="ghost" onClick={() => router.push("/projects")}>
          Cancel
        </Button>
        <div className="flex items-center gap-1.5">
          {step > 1 ? (
            <Button variant="outline" onClick={back} className="gap-1.5">
              <ArrowLeftIcon className="h-3.5 w-3.5" />
              <span>Back</span>
            </Button>
          ) : null}
          {step < STEPS.length ? (
            <Button
              onClick={next}
              disabled={
                (step === 1 && !canStep1Next) ||
                (step === 2 && !canStep2Next) ||
                (step === 3 && !canStep3Next)
              }
              className="gap-1.5"
            >
              <span>Continue</span>
              <ArrowRightIcon className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button
              onClick={handleSubmit}
              disabled={!canSubmit || submitting}
              className="gap-1.5"
            >
              {submitting ? (
                <Loader2Icon className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <SparklesIcon className="h-3.5 w-3.5" />
              )}
              <span>{submitting ? "Creating…" : "Create & dispatch"}</span>
            </Button>
          )}
        </div>
      </footer>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function Header({ step }: { step: number }) {
  return (
    <header>
      <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
        New analysis · step {step} / {STEPS.length}
      </div>
      <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-text-primary">
        <SparklesIcon className="h-5 w-5 text-accent-base" />
        <span>{STEPS[step - 1].label}</span>
      </h1>
      <p className="mt-1 text-sm text-text-secondary">
        提交后会调用{" "}
        <code className="font-mono text-text-primary">POST /api/projects</code> 创建项目，
        再调{" "}
        <code className="font-mono text-text-primary">POST /api/projects/&#123;id&#125;/run</code>{" "}
        启动 Orchestrator · 跳转到新项目的 workspace，DAG 通过 WebSocket 实时推流。
      </p>
    </header>
  );
}

function Stepper({
  step,
  setStep,
}: {
  step: number;
  setStep: (s: number) => void;
}) {
  return (
    <ol className="flex items-center gap-2">
      {STEPS.map((s, i) => {
        const done = step > s.id;
        const active = step === s.id;
        return (
          <li key={s.id} className="flex-1">
            <button
              type="button"
              onClick={() => done && setStep(s.id)}
              disabled={!done && !active}
              className={cn(
                "group flex w-full items-center gap-2 rounded-md border bg-bg-raised px-3 py-2 text-left transition-colors duration-120 ease-out-quart",
                done && "border-success-border hover:bg-success-bg",
                active && "border-accent-border bg-accent-bg",
                !done && !active && "border-border-subtle text-text-muted"
              )}
            >
              <span
                className={cn(
                  "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-pill font-mono text-[10px] font-medium tabular-nums",
                  done && "bg-success-base text-text-inverse",
                  active && "bg-accent-base text-text-inverse",
                  !done && !active && "bg-bg-sunken text-text-muted"
                )}
                data-num
              >
                {done ? <CheckIcon className="h-3 w-3" /> : s.id}
              </span>
              <span
                className={cn(
                  "truncate text-xs font-medium",
                  active ? "text-text-primary" : "text-text-secondary"
                )}
              >
                {s.label}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/* ── Step 1 ─────────────────────────────────────────────────────────── */

function Step1Target({
  target,
  setTarget,
  competitors,
  setCompetitors,
}: {
  target: string;
  setTarget: (s: string) => void;
  competitors: string[];
  setCompetitors: (c: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const inputId = useId();

  const handlePickSample = (t: string) => {
    setTarget(t);
    setCompetitors(SAMPLE_COMPETITORS[t] ?? []);
  };

  const addCompetitor = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || competitors.includes(trimmed) || competitors.length >= 6) {
      return;
    }
    setCompetitors([...competitors, trimmed]);
    setDraft("");
  };

  return (
    <div className="space-y-6">
      <section>
        <label htmlFor={inputId} className="block text-sm font-medium text-text-primary">
          目标产品 (Target)
        </label>
        <p className="mt-0.5 text-xs text-text-muted">
          被分析的主视角产品，所有指标 / claim 以此为锚
        </p>
        <Input
          id={inputId}
          type="text"
          placeholder="如 Notion / Linear / Coda"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="mt-2"
        />
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-text-muted">
            sample
          </span>
          {SAMPLE_TARGETS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => handlePickSample(t)}
              className="rounded-pill border border-border-subtle bg-bg-raised px-2 py-0.5 text-[11px] text-text-secondary hover:border-accent-border hover:text-accent-base"
            >
              {t}
            </button>
          ))}
        </div>
      </section>

      <section>
        <label className="block text-sm font-medium text-text-primary">
          竞品 (Competitors)
          <span className="ml-2 text-[11px] font-normal text-text-muted">
            1 – 6 个 · 已选 {competitors.length}
          </span>
        </label>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {competitors.map((c) => (
            <span
              key={c}
              className="inline-flex items-center gap-1 rounded-pill border border-accent-border bg-accent-bg px-2.5 py-1 text-xs font-medium text-accent-base"
            >
              <span>{c}</span>
              <button
                type="button"
                onClick={() =>
                  setCompetitors(competitors.filter((x) => x !== c))
                }
                aria-label={`remove ${c}`}
              >
                <XIcon className="h-3 w-3" />
              </button>
            </span>
          ))}
          {competitors.length < 6 ? (
            <div className="inline-flex items-center gap-1.5">
              <Input
                type="text"
                placeholder={
                  competitors.length === 0 ? "+ 添加竞品名" : "+ another"
                }
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addCompetitor(draft);
                  }
                }}
                className="h-7 w-[180px] px-2 text-xs"
              />
              {draft ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => addCompetitor(draft)}
                >
                  Add
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

/* ── Step 2 ─────────────────────────────────────────────────────────── */

function Step2Industry({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-text-secondary">
        选择行业 schema 模板。Schema 决定 CompetitorProfile.industry_extension
        的字段集合 + Analyst 的对比维度。
      </p>
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {INDUSTRIES.map((ind) => {
          const Icon = ind.icon;
          const active = value === ind.id;
          return (
            <li key={ind.id}>
              <button
                type="button"
                onClick={() => onChange(ind.id)}
                className={cn(
                  "h-full w-full rounded-md border bg-bg-raised p-4 text-left transition-all duration-120 ease-out-quart",
                  active
                    ? "border-accent-border ring-1 ring-accent-base/15 bg-accent-bg/40"
                    : "border-border-subtle hover:border-border-default"
                )}
              >
                <Icon
                  className={cn(
                    "h-5 w-5",
                    active ? "text-accent-base" : "text-text-muted"
                  )}
                />
                <div className="mt-3 font-medium text-text-primary">
                  {ind.label}
                </div>
                <div className="mt-1 text-[11px] text-text-muted leading-relaxed">
                  {ind.note}
                </div>
                <div className="mt-2 text-[10px] text-text-secondary">
                  示例：{ind.examples}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="text-[11px] text-text-muted">
        新增行业 → 见{" "}
        <code className="font-mono">docs/SCHEMA.md § 2.7</code>，添加扩展模型 +
        注册到 IndustryExtensionUnion。
      </p>
    </div>
  );
}

/* ── Step 3 ─────────────────────────────────────────────────────────── */

function Step3Dimensions({
  value,
  onChange,
}: {
  value: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const toggle = (id: string) => {
    const next = new Set(value);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-text-secondary">
          选择 Analyst 要输出的对比维度（6 选 N，至少 1）。每个维度对应一组独立
          prompt + comparison_matrix。
        </p>
      </div>
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {DIMENSIONS.map((d) => {
          const active = value.has(d.id);
          return (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => toggle(d.id)}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md border bg-bg-raised p-3 text-left transition-colors duration-120 ease-out-quart",
                  active
                    ? "border-accent-border bg-accent-bg/40"
                    : "border-border-subtle hover:border-border-default"
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border",
                    active
                      ? "border-accent-base bg-accent-base"
                      : "border-border-default bg-bg-raised"
                  )}
                >
                  {active ? <CheckIcon className="h-3 w-3 text-text-inverse" /> : null}
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text-primary">
                    {d.label}
                  </div>
                  <div className="text-[11px] text-text-muted">{d.hint}</div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="text-[11px] text-text-muted">
        已选 <span className="font-mono tabular-nums">{value.size}</span> /{" "}
        {DIMENSIONS.length}
      </div>
    </div>
  );
}

/* ── Step 4 ─────────────────────────────────────────────────────────── */

function Step4Mode({
  mode,
  onChangeMode,
  target,
  competitors,
  industry,
  dimensions,
}: {
  mode: string;
  onChangeMode: (m: string) => void;
  target: string;
  competitors: string[];
  industry: string | null;
  dimensions: string[];
}) {
  return (
    <div className="space-y-6">
      <section>
        <div className="text-sm font-medium text-text-primary">运行模式</div>
        <p className="mt-0.5 text-[11px] text-text-muted">
          mode = mock / hybrid / real。Collector 是否走真实抓取 + LLM 是否调用真实
          key
        </p>
        <ul className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
          {MODES.map((m) => {
            const Icon = m.icon;
            const active = mode === m.id;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => onChangeMode(m.id)}
                  className={cn(
                    "h-full w-full rounded-md border bg-bg-raised p-3 text-left transition-colors duration-120 ease-out-quart",
                    active
                      ? "border-accent-border bg-accent-bg/40"
                      : "border-border-subtle hover:border-border-default"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <Icon
                      className={cn(
                        "h-4 w-4",
                        active ? "text-accent-base" : "text-text-muted"
                      )}
                    />
                    <ModeBadge label={m.badge} tone={m.badgeTone} />
                  </div>
                  <div className="mt-2 font-medium text-text-primary">
                    {m.label}
                  </div>
                  <div className="mt-1 text-[11px] text-text-muted leading-relaxed">
                    {m.note}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="rounded-md border border-border-subtle bg-bg-sunken/50 p-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Review
        </div>
        <dl className="mt-2 grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-xs">
          <ReviewItem k="target">
            <span className="font-medium text-text-accent">
              {target || "—"}
            </span>
          </ReviewItem>
          <ReviewItem k="competitors">
            <span className="text-text-primary">
              {competitors.length > 0 ? competitors.join(" · ") : "—"}
            </span>
          </ReviewItem>
          <ReviewItem k="industry">
            <code className="font-mono text-text-secondary">
              {industry ?? "—"}
            </code>
          </ReviewItem>
          <ReviewItem k="dimensions">
            <span className="text-text-secondary">{dimensions.join(", ")}</span>
          </ReviewItem>
          <ReviewItem k="mode">
            <code className="font-mono text-text-primary">{mode}</code>
          </ReviewItem>
        </dl>
        <p className="mt-3 text-[11px] text-text-muted">
          → 点 「Create & dispatch」会模拟 POST /api/projects + Orchestrator.plan() →
          跳转到第一个 run 的 workspace
        </p>
      </section>
    </div>
  );
}

function ReviewItem({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-text-muted">{k}</dt>
      <dd className="break-words">{children}</dd>
    </div>
  );
}

function ModeBadge({
  label,
  tone,
}: {
  label: string;
  tone: "neutral" | "success" | "warning";
}) {
  return (
    <span
      className={cn(
        "rounded-pill border px-1.5 py-0.5 text-[10px] font-medium",
        tone === "neutral" && "border-neutral-border bg-neutral-bg text-neutral-base",
        tone === "success" && "border-success-border bg-success-bg text-success-base",
        tone === "warning" && "border-warning-border bg-warning-bg text-warning-base"
      )}
    >
      {label}
    </span>
  );
}
