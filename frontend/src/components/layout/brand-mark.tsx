import { cn } from "@/lib/utils";

/**
 * Atlas 品牌 mark —— 六边形 honeycomb 几何感（参考 AgentResearch 风格）。
 *
 * 7 个小六边形（1 中心 + 6 周围）排成蜂窝；按 accent-base / 紫罗兰族 + 桃色高光
 * 着色。SVG 内联，配合 currentColor 受 className/text-* 控制对比度。
 */
export function BrandMark({
  className,
}: {
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Atlas logo"
      className={cn("shrink-0", className)}
    >
      <defs>
        <linearGradient id="brand-tile-a" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="oklch(58% 0.22 285)" />
          <stop offset="100%" stopColor="oklch(50% 0.24 285)" />
        </linearGradient>
        <linearGradient id="brand-tile-b" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="oklch(64% 0.22 305)" />
          <stop offset="100%" stopColor="oklch(56% 0.22 295)" />
        </linearGradient>
        <linearGradient id="brand-tile-c" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="oklch(70% 0.20 30)" />
          <stop offset="100%" stopColor="oklch(62% 0.22 25)" />
        </linearGradient>
      </defs>

      {/* 6 outer tiles + 1 center, hex grid */}
      {/* center */}
      <polygon
        points="16,10 19.46,12 19.46,16 16,18 12.54,16 12.54,12"
        fill="url(#brand-tile-b)"
      />
      {/* top */}
      <polygon
        points="16,2 19.46,4 19.46,8 16,10 12.54,8 12.54,4"
        fill="url(#brand-tile-a)"
      />
      {/* top-right */}
      <polygon
        points="23,6 26.46,8 26.46,12 23,14 19.54,12 19.54,8"
        fill="url(#brand-tile-a)"
      />
      {/* bottom-right */}
      <polygon
        points="23,18 26.46,20 26.46,24 23,26 19.54,24 19.54,20"
        fill="url(#brand-tile-c)"
      />
      {/* bottom */}
      <polygon
        points="16,22 19.46,24 19.46,28 16,30 12.54,28 12.54,24"
        fill="url(#brand-tile-b)"
      />
      {/* bottom-left */}
      <polygon
        points="9,18 12.46,20 12.46,24 9,26 5.54,24 5.54,20"
        fill="url(#brand-tile-a)"
      />
      {/* top-left */}
      <polygon
        points="9,6 12.46,8 12.46,12 9,14 5.54,12 5.54,8"
        fill="url(#brand-tile-c)"
      />
    </svg>
  );
}
