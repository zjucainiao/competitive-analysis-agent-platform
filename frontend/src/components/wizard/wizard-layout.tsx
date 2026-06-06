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
  UserSearchIcon,
  UsersIcon,
  ZapIcon,
  PlusIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { emitIntervention } from "@/lib/workspace-actions";
import {
  createProject,
  describeError,
  discoverCompetitors,
  startRun,
  ApiError,
} from "@/lib/api/client";
import { revalidate } from "@/lib/api/hooks";
import type {
  AnalysisDimension,
  AnalysisMode,
  DiscoveredCompetitor,
} from "@/lib/api/types";

/* ── option definitions ──────────────────────────────────────────────── */

const INDUSTRIES = [
  {
    id: "collaboration_saas",
    label: "协作办公 SaaS",
    examples: "Notion · ClickUp · Asana · Lark",
    icon: GlobeIcon,
    note: "任务 / 文档 / 看板 / 自动化",
    available: true,
  },
  {
    id: "crm_saas",
    label: "CRM SaaS",
    examples: "Salesforce · HubSpot · Pipedrive",
    icon: BuildingIcon,
    note: "线索 / 商机管道 / 客户生命周期",
    available: true,
  },
  {
    id: "cross_border_ecommerce_saas",
    label: "跨境电商 SaaS",
    examples: "Shopify · BigCommerce · Wix Stores",
    icon: ShoppingCartIcon,
    note: "店铺 / 支付 / 物流 / 多语言",
    available: true,
  },
  {
    id: "edu_saas",
    label: "教育 SaaS",
    examples: "Canvas · Blackboard · 钉钉教育 · 学习通",
    icon: GraduationCapIcon,
    note: "课程 / 测评 / 课堂 / 数据分析",
    available: true,
  },
];

const DIMENSIONS = [
  { id: "feature_comparison", label: "功能对比", hint: "功能对比矩阵" },
  { id: "pricing_comparison", label: "定价对比", hint: "定价档与溢价" },
  { id: "user_feedback", label: "用户口碑", hint: "G2 / Capterra 评价洞察" },
  { id: "swot", label: "SWOT", hint: "围绕目标产品视角" },
  { id: "differentiation_opportunities", label: "差异化机会", hint: "可切入的差异化点" },
  { id: "positioning", label: "市场定位", hint: "定位 / 目标用户重叠" },
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

const ANALYSIS_MODES: Array<{
  id: AnalysisMode;
  label: string;
  icon: typeof UsersIcon;
  note: string;
}> = [
  {
    id: "competitive_compare",
    label: "竞品对比",
    icon: UsersIcon,
    note: "1+ 个竞品，跑标准对比维度（推荐）",
  },
  {
    id: "single_research",
    label: "单产品调研",
    icon: UserSearchIcon,
    note: "只分析一个产品，不做对比",
  },
  {
    id: "auto_discover",
    label: "自动发现竞品",
    icon: ZapIcon,
    note: "让 LLM 推荐竞品，你可编辑后再跑",
  },
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
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>(
    "competitive_compare"
  );
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

  // 切换 analysis_mode 时清理 competitors（单产品调研下竞品无意义）。
  // dimensions 不再因 mode 改动 —— 单产品也能选所有维度（Analyst 内部走单产品分支）。
  const handleSwitchAnalysisMode = (next: AnalysisMode) => {
    setAnalysisMode(next);
    if (next === "single_research") {
      setCompetitors([]);
    }
  };

  // Step 1 校验：随 analysis_mode 切换
  const canStep1Next = (() => {
    if (target.trim().length === 0) return false;
    if (analysisMode === "single_research") return true; // 0 竞品也行
    return competitors.length >= 1; // compare / auto_discover 都需要 ≥1
  })();
  const canStep2Next = industry !== null;
  const canStep3Next = dimensions.size >= 1;
  const canSubmit =
    canStep1Next && canStep2Next && canStep3Next && mode === "real";

  const next = () => setStep((s) => Math.min(STEPS.length, s + 1));
  const back = () => setStep((s) => Math.max(1, s - 1));

  const handleSubmit = async () => {
    if (submitting) return;
    setSubmitting(true);
    const projectName =
      analysisMode === "single_research"
        ? `${target} 单产品调研`
        : `${target} vs ${competitors.join(" / ")}`;
    const payload = {
      project_name: projectName,
      // owner 由后端从登录用户派生
      target_product: target,
      competitors: analysisMode === "single_research" ? [] : competitors,
      analysis_mode: analysisMode,
      industry: industry!,
      analysis_dimensions: Array.from(dimensions) as AnalysisDimension[],
      report_template_id:
        analysisMode === "single_research"
          ? "single_research_v1"
          : "standard_v1",
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
          toast.warning("已有一次运行在进行中，跳到工作区查看");
        } else {
          toast.error("启动分析失败", { description: describeError(e) });
        }
      }
      void revalidate.projects();
      router.push(`/projects/${project.project_id}/runs/${project.project_id}?tab=dag`);
    } catch (e) {
      toast.error("创建项目失败", { description: describeError(e) });
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
            analysisMode={analysisMode}
            onChangeAnalysisMode={handleSwitchAnalysisMode}
            target={target}
            setTarget={setTarget}
            competitors={competitors}
            setCompetitors={setCompetitors}
            industry={industry}
          />
        )}
        {step === 2 && <Step2Industry value={industry} onChange={setIndustry} />}
        {step === 3 && (
          <Step3Dimensions
            value={dimensions}
            onChange={setDimensions}
            analysisMode={analysisMode}
          />
        )}
        {step === 4 && (
          <Step4Mode
            mode={mode}
            onChangeMode={setMode}
            analysisMode={analysisMode}
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
  analysisMode,
  onChangeAnalysisMode,
  target,
  setTarget,
  competitors,
  setCompetitors,
  industry,
}: {
  analysisMode: AnalysisMode;
  onChangeAnalysisMode: (m: AnalysisMode) => void;
  target: string;
  setTarget: (s: string) => void;
  competitors: string[];
  setCompetitors: (c: string[]) => void;
  industry: string | null;
}) {
  const [draft, setDraft] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredCompetitor[]>([]);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const inputId = useId();

  const handlePickSample = (t: string) => {
    setTarget(t);
    if (analysisMode !== "single_research") {
      setCompetitors(SAMPLE_COMPETITORS[t] ?? []);
    }
  };

  const addCompetitor = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || competitors.includes(trimmed) || competitors.length >= 6) {
      return;
    }
    setCompetitors([...competitors, trimmed]);
    setDraft("");
  };

  const handleDiscover = async () => {
    if (!target.trim() || discovering) return;
    setDiscovering(true);
    setDiscoverError(null);
    setDiscovered([]);
    try {
      const resp = await discoverCompetitors({
        target_product: target.trim(),
        industry: industry ?? "collaboration_saas",
        max_competitors: 5,
      });
      if (resp.error) {
        setDiscoverError(resp.error);
        toast.warning("LLM 推荐失败", { description: resp.error });
      } else if (resp.competitors.length === 0) {
        setDiscoverError("LLM 没有找到该产品的常见竞品，请手动输入");
      } else {
        setDiscovered(resp.competitors);
        toast.success(`LLM 推荐了 ${resp.competitors.length} 个竞品`, {
          description: "点击 + 添加到列表，可编辑或删除",
        });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setDiscoverError(msg);
      toast.error("调用失败", { description: msg });
    } finally {
      setDiscovering(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* ── 分析模式选择 ── */}
      <section>
        <div className="text-sm font-medium text-text-primary">分析模式</div>
        <p className="mt-0.5 text-[11px] text-text-muted">
          决定 DAG 形态：是否需要竞品 / Reporter 用对比基调还是调研基调
        </p>
        <ul className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
          {ANALYSIS_MODES.map((m) => {
            const Icon = m.icon;
            const active = analysisMode === m.id;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => onChangeAnalysisMode(m.id)}
                  className={cn(
                    "h-full w-full rounded-md border bg-bg-raised p-3 text-left transition-colors duration-120 ease-out-quart",
                    active
                      ? "border-accent-border ring-1 ring-accent-base/15 bg-accent-bg/40"
                      : "border-border-subtle hover:border-border-default"
                  )}
                >
                  <Icon
                    className={cn(
                      "h-4 w-4",
                      active ? "text-accent-base" : "text-text-muted"
                    )}
                  />
                  <div className="mt-2 text-sm font-medium text-text-primary">
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

      {/* ── 目标产品 ── */}
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

      {/* ── 竞品输入（按 analysis_mode 条件渲染） ── */}
      {analysisMode === "single_research" ? (
        <section className="rounded-md border border-dashed border-border-default bg-bg-sunken/40 p-4">
          <div className="flex items-center gap-2">
            <UserSearchIcon className="h-4 w-4 text-accent-base" />
            <span className="text-sm font-medium text-text-primary">
              单产品调研模式
            </span>
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-text-muted">
            不需要竞品。Collector / Extractor 只跑目标产品一路；Analyst 全部 6 个维度
            都能选 ——「功能 / 定价 / SWOT / 差异化」走「单产品视角」分支（功能能力速览 /
            定价档位画像 / 自我 SW 评估 / 差异化定位）；Reporter 用调研基调模板，
            不会出现「X vs Y」对比段落。
          </p>
        </section>
      ) : (
        <section>
          <label className="block text-sm font-medium text-text-primary">
            竞品 (Competitors)
            <span className="ml-2 text-[11px] font-normal text-text-muted">
              1 – 6 个 · 已选 {competitors.length}
            </span>
          </label>

          {analysisMode === "auto_discover" ? (
            <div className="mt-2 rounded-md border border-accent-border/60 bg-accent-bg/30 p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-[11px] text-text-secondary">
                  让 LLM 根据目标产品 + 行业推荐 3-5 个常见竞品（可编辑 / 删除）
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleDiscover}
                  disabled={!target.trim() || discovering}
                  className="shrink-0 gap-1.5"
                >
                  {discovering ? (
                    <Loader2Icon className="h-3 w-3 animate-spin" />
                  ) : (
                    <ZapIcon className="h-3 w-3" />
                  )}
                  <span>
                    {discovering
                      ? "调用中…"
                      : discovered.length > 0
                        ? "重新推荐"
                        : "让 LLM 推荐"}
                  </span>
                </Button>
              </div>
              {discoverError ? (
                <div className="mt-2 text-[11px] text-warning-base">
                  {discoverError}
                </div>
              ) : null}
              {discovered.length > 0 ? (
                <ul className="mt-3 space-y-1.5">
                  {discovered.map((d) => {
                    const alreadyAdded = competitors.includes(d.name);
                    return (
                      <li
                        key={d.name}
                        className="flex items-start gap-2 rounded-md border border-border-subtle bg-bg-raised px-2.5 py-1.5"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-medium text-text-primary">
                            {d.name}
                          </div>
                          <div className="text-[10px] text-text-muted leading-relaxed">
                            {d.reason}
                          </div>
                        </div>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={alreadyAdded || competitors.length >= 6}
                          onClick={() => addCompetitor(d.name)}
                          className="h-6 shrink-0 gap-1 px-2 text-[11px]"
                        >
                          {alreadyAdded ? (
                            <CheckIcon className="h-3 w-3" />
                          ) : (
                            <PlusIcon className="h-3 w-3" />
                          )}
                          <span>{alreadyAdded ? "已加" : "加入"}</span>
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          ) : null}

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
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
      )}
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
        选择目标产品所在的行业，会用对应行业的对比维度和字段集合。
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
  analysisMode,
}: {
  value: Set<string>;
  onChange: (next: Set<string>) => void;
  analysisMode: AnalysisMode;
}) {
  const isSingle = analysisMode === "single_research";
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
          {isSingle
            ? "单产品调研模式下，所有维度都可选。Analyst 会用「单产品视角」处理：functions 变成功能能力速览、pricing 变成定价档位画像、SWOT 只评 Strengths/Weaknesses、differentiation 描述自我差异化定位 —— 不会写「X vs Y」段落。"
            : "选择 Analyst 要输出的对比维度（6 选 N，至少 1）。每个维度对应一组独立 prompt + comparison_matrix。"}
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
                  {active ? (
                    <CheckIcon className="h-3 w-3 text-text-inverse" />
                  ) : null}
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
  analysisMode,
  target,
  competitors,
  industry,
  dimensions,
}: {
  mode: string;
  onChangeMode: (m: string) => void;
  analysisMode: AnalysisMode;
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
          分析全程使用真实 LLM + 真实网页采集，产出带原文引用的报告。
        </p>
        <div className="mt-3 flex items-start gap-3 rounded-md border border-accent-border bg-accent-bg/30 p-3">
          <ServerCogIcon className="mt-0.5 h-4 w-4 shrink-0 text-accent-base" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-text-primary">Real</span>
              <ModeBadge label="burns key" tone="warning" />
            </div>
            <div className="mt-1 text-[11px] leading-relaxed text-text-muted">
              全真 LLM + 真采集，会消耗 API key 额度。
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-md border border-border-subtle bg-bg-sunken/50 p-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Review
        </div>
        <dl className="mt-2 grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-xs">
          <ReviewItem k="analysis_mode">
            <code className="font-mono text-text-accent">{analysisMode}</code>
          </ReviewItem>
          <ReviewItem k="target">
            <span className="font-medium text-text-accent">
              {target || "—"}
            </span>
          </ReviewItem>
          <ReviewItem k="competitors">
            <span className="text-text-primary">
              {analysisMode === "single_research"
                ? "—（单产品调研，无竞品）"
                : competitors.length > 0
                  ? competitors.join(" · ")
                  : "—"}
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
          <ReviewItem k="report_tpl">
            <code className="font-mono text-text-secondary">
              {analysisMode === "single_research"
                ? "single_research_v1"
                : "standard_v1"}
            </code>
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
