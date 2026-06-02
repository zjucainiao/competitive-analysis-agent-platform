/**
 * Workspace 路由的 loading skeleton。
 * Next.js 16 自动在 async route 之上挂这个。
 */
export default function Loading() {
  return (
    <div className="min-h-full bg-background">
      {/* nav */}
      <div className="sticky top-0 z-30 h-13 border-b border-border-subtle bg-background">
        <div className="mx-auto flex h-full max-w-[1600px] items-center gap-8 px-10">
          <Skel className="h-4 w-16" />
          <Skel className="h-4 w-20" />
          <Skel className="h-4 w-20" />
          <div className="ml-auto flex items-center gap-3">
            <Skel className="h-8 w-32 rounded-md" />
            <Skel className="h-8 w-8 rounded-full" />
          </div>
        </div>
      </div>
      {/* context bar */}
      <div className="border-b border-border-subtle bg-background">
        <div className="mx-auto flex h-[84px] max-w-[1600px] flex-col justify-center gap-2 px-10">
          <div className="flex items-center gap-3">
            <Skel className="h-3.5 w-16" />
            <Skel className="h-3.5 w-32" />
            <Skel className="h-3.5 w-20" />
            <Skel className="h-5 w-24 rounded-pill ml-2" />
            <div className="ml-auto flex items-center gap-1.5">
              <Skel className="h-7 w-20 rounded-md" />
              <Skel className="h-7 w-20 rounded-md" />
              <Skel className="h-7 w-28 rounded-md" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Skel className="h-3 w-32" />
            <Skel className="h-3 w-40" />
            <Skel className="h-3 w-32" />
          </div>
        </div>
      </div>
      {/* tabs */}
      <div className="border-b border-border-subtle bg-background">
        <div className="mx-auto flex h-11 max-w-[1600px] items-center gap-4 px-10">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skel key={i} className="h-4 w-16" />
          ))}
        </div>
      </div>
      {/* body */}
      <main className="mx-auto max-w-[1600px] px-10 py-8">
        <div className="rounded-lg border border-border-subtle bg-bg-raised p-6">
          <Skel className="h-4 w-64 mb-4" />
          <Skel className="h-[600px] w-full rounded-md" />
        </div>
      </main>
    </div>
  );
}

function Skel({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse-soft rounded bg-bg-sunken ${className}`}
      aria-hidden
    />
  );
}
