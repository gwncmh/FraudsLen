import * as React from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function Progress({ value = 0, max = 100, className, indicatorClassName }) {
  const p = max ? Math.min(100, Math.max(0, (100 * value) / max)) : 0;
  return (
    <div className={cn("relative h-2 w-full overflow-hidden rounded-full bg-secondary", className)}>
      <motion.div className={cn("h-full rounded-full bg-primary", indicatorClassName)}
        initial={false} animate={{ width: `${p}%` }} transition={{ duration: 0.5, ease: "easeOut" }} />
    </div>
  );
}

export function Separator({ className, vertical }) {
  return <div className={cn("shrink-0 bg-border", vertical ? "h-full w-px" : "h-px w-full", className)} />;
}

export function Skeleton({ className }) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
}

export function Empty({ icon: Icon, title, children, className }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-10 text-center text-muted-foreground", className)}>
      {Icon && <div className="rounded-full bg-muted p-3"><Icon className="size-5" /></div>}
      {title && <p className="font-medium text-foreground">{title}</p>}
      {children && <div className="max-w-md text-xs">{children}</div>}
    </div>
  );
}

export function Alert({ variant = "info", icon: Icon, title, children, className }) {
  const tone = {
    info: "border-primary/30 bg-primary/5 text-foreground [&_svg]:text-primary",
    warn: "border-medium/40 bg-medium/10 text-foreground [&_svg]:text-medium",
    error: "border-destructive/40 bg-destructive/10 text-foreground [&_svg]:text-destructive",
  }[variant];
  return (
    <div className={cn("flex gap-3 rounded-lg border px-4 py-3 text-sm", tone, className)}>
      {Icon && <Icon className="mt-0.5 size-4 shrink-0" />}
      <div className="space-y-1">{title && <p className="font-medium">{title}</p>}<div className="text-muted-foreground">{children}</div></div>
    </div>
  );
}

export function Json({ data, className }) {
  return (
    <pre className={cn("max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-950 p-3 font-mono text-xs text-zinc-200", className)}>
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

/** Danh sách nhãn – giá trị dạng lưới hai cột. */
export function DataList({ items, className }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-2 text-sm", className)}>
      {items.filter(Boolean).map(([k, v]) => (
        <React.Fragment key={k}><dt className="text-muted-foreground">{k}</dt><dd className="min-w-0 break-words">{v ?? "–"}</dd></React.Fragment>
      ))}
    </dl>
  );
}
