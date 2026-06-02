"use client";

import { ReportParagraph } from "./report-paragraph";
import type { MockSection } from "@/lib/report-mock";

export function ReportSection({
  section,
  globalParagraphStart,
  showV2,
  focusedParagraphId,
  onEvidenceClick,
  onFocusEvidence,
  onSaved,
}: {
  section: MockSection;
  globalParagraphStart: number;
  showV2: boolean;
  focusedParagraphId: string | null;
  onEvidenceClick: (evidenceId: string) => void;
  onFocusEvidence: (evidenceId: string | null) => void;
  onSaved: (paragraphId: string, newText: string) => void;
}) {
  return (
    <section
      id={`sec-${section.id}`}
      className="scroll-mt-[152px]"
      aria-labelledby={`heading-${section.id}`}
    >
      <header className="mb-3 flex items-baseline gap-3">
        <span className="font-mono text-sm font-medium text-accent-base tabular-nums">
          {section.number}
        </span>
        <h2
          id={`heading-${section.id}`}
          className="text-lg font-semibold text-text-primary"
        >
          {section.title}
        </h2>
      </header>

      <div className="space-y-1">
        {section.paragraphs.map((p, i) => (
          <ReportParagraph
            key={p.id}
            paragraph={p}
            index={globalParagraphStart + i}
            showV2={showV2}
            isFocused={p.id === focusedParagraphId}
            onEvidenceClick={onEvidenceClick}
            onFocusEvidence={onFocusEvidence}
            onSaved={onSaved}
          />
        ))}
      </div>
    </section>
  );
}
