import { GlobalNav } from "@/components/layout/global-nav";
import { GlobalMetricsDashboard } from "@/components/metrics/global-metrics-dashboard";

/**
 * /metrics · 全局指标仪表盘（跨项目 / 跨时间）。
 * v1 mock · Sprint 2 接 GlobalMetricsService.aggregate()。
 */
export default function MetricsPage() {
  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <div className="mx-auto max-w-6xl px-10 py-10">
        <GlobalMetricsDashboard />
      </div>
    </div>
  );
}
