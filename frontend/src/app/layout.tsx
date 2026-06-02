import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { ShortcutsHelp } from "@/components/layout/shortcuts-help";
import "./globals.css";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Atlas · 竞品分析 Agent 协作平台",
  description:
    "AI 驱动的 B 端 SaaS 竞品分析平台：5 个专职 Agent · DAG 编排 · 证据链可溯源 · 质检反馈闭环 · 全链路可观测",
};

/**
 * Inline 主题初始化脚本：在 React 注水前同步 .dark class。
 * 防止 dark → light 闪烁。
 *
 * 优先级：localStorage > prefers-color-scheme > light (default)
 */
const themeInitScript = `
(function() {
  try {
    var stored = localStorage.getItem('atlas:theme');
    if (stored === 'dark' || (!stored && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
      document.documentElement.classList.add('dark');
    }
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${inter.variable} ${jetBrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{ __html: themeInitScript }}
        />
      </head>
      <body className="bg-background text-foreground min-h-full flex flex-col">
        <TooltipProvider delay={150}>{children}</TooltipProvider>
        <ShortcutsHelp />
        <Toaster />
      </body>
    </html>
  );
}
