import { MOCK_REPORT, getEvidence, type MockSection } from "./report-mock";

/**
 * 把 MockReport 渲染为「读者向」Markdown 交付物。
 *
 * 设计原则（与后端 _render_markdown 对齐）：下载的文件是给人看的成品，
 * 不含任何内部标签 —— evidence_id 渲染成编号引用 `[n]` + 末尾「数据来源」列表，
 * soft conclusion / quantitative / QA issue / v2 这些是屏幕视图的溯源标记，
 * 不进交付物。用户改过的段落静默采用其文本，不打「user-edited」标。
 */

export function renderReportAsMarkdown(
  localEdits: Record<string, string> = {},
  showV2 = false
): string {
  const lines: string[] = [];
  const r = MOCK_REPORT;

  lines.push(`# ${r.target} vs ${r.competitors.join("、")}`);
  lines.push("");
  lines.push(`> 竞品分析报告 · ${r.generatedAt.slice(0, 10)}`);
  lines.push("");
  if (r.summary) {
    lines.push("## 摘要");
    lines.push("");
    lines.push(r.summary);
    lines.push("");
  }

  // 引用编号：按正文首次出现顺序分配 [1][2]...，只给能溯源到的 evidence 编号
  const citeNo = new Map<string, number>();
  const cite = (eid: string): number | null => {
    if (!getEvidence(eid)) return null;
    if (!citeNo.has(eid)) citeNo.set(eid, citeNo.size + 1);
    return citeNo.get(eid)!;
  };

  r.sections.forEach((section) => {
    lines.push(`## ${section.number}. ${section.title}`);
    lines.push("");
    section.paragraphs.forEach((p) => {
      const text =
        localEdits[p.id] ?? (showV2 && p.pendingV2 ? p.pendingV2.text : p.text);
      const evidenceIds =
        showV2 && p.pendingV2 ? p.pendingV2.evidenceIds : p.evidenceIds;
      const markers = Array.from(new Set(evidenceIds))
        .map((eid) => cite(eid))
        .filter((n): n is number => n !== null)
        .map((n) => `[${n}]`)
        .join("");
      lines.push(markers ? `${text} ${markers}` : text);
      lines.push("");
    });
  });

  // 数据来源：只列正文真正引用到的，按引用号排序，人类可读
  if (citeNo.size > 0) {
    lines.push("## 数据来源");
    lines.push("");
    Array.from(citeNo.entries())
      .sort((a, b) => a[1] - b[1])
      .forEach(([eid, n]) => {
        const ev = getEvidence(eid);
        if (!ev) return;
        lines.push(`${n}. ${ev.product} · ${ev.sourceLabel} — ${ev.sourceUrl}`);
      });
    lines.push("");
  }

  /* compliance disclaimer (per docs/COMPLIANCE.md § 3.3) */
  lines.push("---");
  lines.push("");
  lines.push(
    `> 数据来源声明：本报告基于公开渠道于 ${r.generatedAt.slice(0, 10)} 采集的` +
      "公开资料（各产品官网 / 帮助文档 / G2 · Capterra 公开评价 / 官方博客与更新日志）生成，" +
      "已遵守各站点 robots.txt 与服务条款；未含个人隐私或非公开内容。" +
      "结论为基于公开资料的 AI 分析推断，不构成投资 / 采购建议。"
  );

  return lines.join("\n");
}

/** 在浏览器中触发下载 */
export function downloadMarkdown(filename: string, content: string): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 简化的 section 计数（report-layout.tsx 用） */
export function countLocalEdits(localEdits: Record<string, string>): number {
  return Object.keys(localEdits).length;
}

export { type MockSection };
