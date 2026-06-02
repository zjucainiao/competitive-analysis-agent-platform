import { MOCK_REPORT, MOCK_EVIDENCES, type MockEvidence } from "./report-mock";

/**
 * Evidence 反向索引：evidence_id → 引用它的段落列表
 * 评分项「信息溯源完整 · 反向定位」核心数据。
 */
export interface ParagraphRef {
  paragraphId: string;
  sectionId: string;
  sectionNumber: string;
  sectionTitle: string;
  textPreview: string;
}

export function buildReverseIndex(): Record<string, ParagraphRef[]> {
  const index: Record<string, ParagraphRef[]> = {};
  MOCK_REPORT.sections.forEach((s) => {
    s.paragraphs.forEach((p) => {
      p.evidenceIds.forEach((eid) => {
        if (!index[eid]) index[eid] = [];
        index[eid].push({
          paragraphId: p.id,
          sectionId: s.id,
          sectionNumber: s.number,
          sectionTitle: s.title,
          textPreview: p.text.slice(0, 80) + (p.text.length > 80 ? "…" : ""),
        });
      });
      /* pendingV2 evidence_ids 也算反向引用（v2 即将引用） */
      if (p.pendingV2) {
        p.pendingV2.evidenceIds.forEach((eid) => {
          if (!index[eid]) index[eid] = [];
          if (!index[eid].some((r) => r.paragraphId === p.id)) {
            index[eid].push({
              paragraphId: p.id,
              sectionId: s.id,
              sectionNumber: s.number,
              sectionTitle: s.title,
              textPreview:
                "[v2 pending] " +
                p.pendingV2!.text.slice(0, 70) +
                (p.pendingV2!.text.length > 70 ? "…" : ""),
            });
          }
        });
      }
    });
  });
  return index;
}

export function listEvidences(): MockEvidence[] {
  return Object.values(MOCK_EVIDENCES);
}

/* derived facets for filter */
export function listProducts(): string[] {
  return Array.from(new Set(Object.values(MOCK_EVIDENCES).map((e) => e.product)));
}

export function listSourceTypes(): string[] {
  return Array.from(
    new Set(Object.values(MOCK_EVIDENCES).map((e) => e.sourceType))
  );
}
