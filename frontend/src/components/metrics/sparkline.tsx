"use client";

/**
 * 极简 SVG sparkline。无库依赖。
 *  - 默认宽 80 高 20
 *  - 上下 1px 内 padding
 *  - 颜色 = currentColor，由父级用 text-* 控制
 */
export function Sparkline({
  values,
  width = 80,
  height = 20,
  strokeWidth = 1.4,
  className,
}: {
  values: number[];
  width?: number;
  height?: number;
  strokeWidth?: number;
  className?: string;
}) {
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const padY = 1.5;
  const innerH = height - padY * 2;
  const stepX = values.length > 1 ? width / (values.length - 1) : 0;

  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = padY + (1 - (v - min) / range) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const path = `M ${points.join(" L ")}`;
  const lastX = (values.length - 1) * stepX;
  const lastY = padY + (1 - (values[values.length - 1] - min) / range) * innerH;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r={1.6} fill="currentColor" />
    </svg>
  );
}
