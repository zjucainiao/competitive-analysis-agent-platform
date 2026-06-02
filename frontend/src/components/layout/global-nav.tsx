import Link from "next/link";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { CmdTrigger } from "./cmd-trigger";
import { ThemeToggle } from "./theme-toggle";

/**
 * 顶导航 52px。整站常驻。
 *  - 左：Atlas 品牌标 + 顶级链接
 *  - 右：⌘K 命令面板 + 头像占位（v1 不接 auth）
 */
export function GlobalNav() {
  return (
    <header className="sticky top-0 z-30 h-13 border-b border-border-subtle bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/80">
      <div className="mx-auto flex h-full max-w-[1600px] items-center gap-8 px-10">
        <Link
          href="/"
          className="group inline-flex items-center gap-2 text-text-primary"
        >
          <span className="h-1.5 w-1.5 rounded-pill bg-accent-base transition-transform duration-120 ease-out-quart group-hover:scale-125" />
          <span className="text-sm font-semibold tracking-tight">Atlas</span>
        </Link>

        <nav className="flex items-center gap-1" aria-label="primary">
          <NavLink href="/projects">Projects</NavLink>
          <NavLink href="/metrics">Metrics</NavLink>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <CmdTrigger />
          <ThemeToggle />
          <Avatar className="h-8 w-8 border border-border-default">
            <AvatarFallback className="bg-bg-sunken text-xs font-medium text-text-secondary">
              XF
            </AvatarFallback>
          </Avatar>
        </div>
      </div>
    </header>
  );
}

function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="rounded-md px-2.5 py-1.5 text-sm text-text-secondary transition-colors duration-120 ease-out-quart hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
    >
      {children}
    </Link>
  );
}
