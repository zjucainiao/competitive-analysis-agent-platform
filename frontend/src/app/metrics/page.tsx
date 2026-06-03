import { SidebarShell } from "@/components/layout/sidebar-shell";
import { GlobalMetricsDashboard } from "@/components/metrics/global-metrics-dashboard";

/**
 * /metrics · 全局指标仪表盘（跨项目 / 跨时间）。
 */
export default function MetricsPage() {
  return (
    <SidebarShell
      topBarLeft={
        <div className="text-xs text-text-muted">
          <span className="font-medium text-text-secondary">全局指标</span>
        </div>
      }
    >
      <div className="mx-auto max-w-6xl">
        <GlobalMetricsDashboard />
      </div>
    </SidebarShell>
  );
}
