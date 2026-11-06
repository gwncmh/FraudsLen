import React from "react";
import { motion } from "framer-motion";
import { AlertOctagon, AlertTriangle, CircleHelp, ShieldAlert, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CASE_STATUS, FEATURE_LABELS, cn, num } from "@/lib/utils";

const SEV = {
  CRITICAL: { label: "Critical", icon: AlertOctagon },
  HIGH: { label: "High", icon: ShieldAlert },
  MEDIUM: { label: "Medium", icon: AlertTriangle },
  LOW: { label: "Low", icon: ShieldCheck },
  UNSCORED: { label: "Unscored", icon: CircleHelp },
};

export function SeverityBadge({ level, className }) {
  const s = SEV[level] || SEV.LOW;
  const Icon = s.icon;
  return <Badge variant={level in SEV ? level : "outline"} className={className}><Icon />{s.label}</Badge>;
}

export const sevColor = (level) => ({
  CRITICAL: "hsl(var(--critical))", HIGH: "hsl(var(--high))", MEDIUM: "hsl(var(--medium))",
  LOW: "hsl(var(--low))", UNSCORED: "hsl(var(--unscored))",
}[level] || "hsl(var(--muted-foreground))");

/** Thanh dọc màu bên trái thẻ theo mức nghiêm trọng. */
export function SeverityRail({ level }) {
  return <span className="absolute inset-y-0 left-0 w-1 rounded-l-xl" style={{ background: sevColor(level) }} />;
}

export function CaseStatusBadge({ status }) {
  const v = status === "ESCALATED" ? "HIGH" : status === "INVESTIGATING" ? "info" : status?.startsWith("CLOSED") ? "secondary" : "outline";
  return <Badge variant={v}>{CASE_STATUS[status] || status}</Badge>;
}

export function LiveDot({ ok = true, className }) {
  return (
    <span className={cn("relative inline-flex size-2.5", className)}>
      {ok && <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-low" />}
      <span className={cn("relative inline-flex size-2.5 rounded-full", ok ? "bg-low" : "bg-destructive")} />
    </span>
  );
}

/** Thang log 1e-4..1 để thấy rõ vùng điểm thấp. */
const pos = (v) => Math.min(100, Math.max(0, ((Math.log10(Math.max(v ?? 1e-4, 1e-4)) + 4) / 4) * 100));

const ROUTE_COLOR = { HIGH: "hsl(var(--high))", MEDIUM: "hsl(var(--medium))", LOW: "hsl(var(--low))", UNSCORED: "hsl(var(--unscored))" };

export function RiskScoreBar({ score, tMed, tHigh, route, compact }) {
  if (score == null) return <span className="text-xs text-muted-foreground">không có điểm</span>;
  return (
    <div className={cn("w-full", compact ? "min-w-[90px]" : "")}>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-secondary">
        {tMed != null && <>
          <div className="absolute inset-y-0 bg-medium/30" style={{ left: `${pos(tMed)}%`, width: `${pos(tHigh) - pos(tMed)}%` }} />
          <div className="absolute inset-y-0 right-0 bg-high/30" style={{ left: `${pos(tHigh)}%` }} />
        </>}
        <motion.div className="absolute inset-y-0 left-0 rounded-full"
          style={{ background: tMed == null && route ? ROUTE_COLOR[route] : score >= (tHigh ?? 1) ? "hsl(var(--high))" : score >= (tMed ?? 1) ? "hsl(var(--medium))" : "hsl(var(--low))" }}
          initial={{ width: 0 }} animate={{ width: `${pos(score)}%` }} transition={{ duration: 0.6, ease: "easeOut" }} />
      </div>
      {!compact && <div className="mt-1 flex justify-between text-[10px] text-muted-foreground tabular"><span>0,0001</span><span>0,001</span><span>0,01</span><span>0,1</span><span>1</span></div>}
    </div>
  );
}

/** Đồng hồ bán nguyệt cho điểm rủi ro (0–100 theo thang log). */
export function RiskGauge({ score, tMed, tHigh, size = 168 }) {
  const r = 70, c = Math.PI * r;
  const p = score == null ? 0 : pos(score) / 100;
  const color = score == null ? "hsl(var(--unscored))" : score >= (tHigh ?? 1) ? "hsl(var(--high))" : score >= (tMed ?? 1) ? "hsl(var(--medium))" : "hsl(var(--low))";
  const tick = (t) => {
    const a = Math.PI * (1 - pos(t) / 100);
    return { x1: 90 + Math.cos(a) * 60, y1: 90 - Math.sin(a) * 60, x2: 90 + Math.cos(a) * 82, y2: 90 - Math.sin(a) * 82 };
  };
  return (
    <div className="relative" style={{ width: size, height: size * 0.62 }}>
      <svg viewBox="0 0 180 110" className="h-full w-full">
        <path d="M20 90 A70 70 0 0 1 160 90" fill="none" stroke="hsl(var(--secondary))" strokeWidth="14" strokeLinecap="round" />
        <motion.path d="M20 90 A70 70 0 0 1 160 90" fill="none" stroke={color} strokeWidth="14" strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - p) }} transition={{ duration: 0.9, ease: "easeOut" }} />
        {tMed != null && [tMed, tHigh].map((t, i) => <line key={i} {...tick(t)} stroke="hsl(var(--foreground))" strokeOpacity=".35" strokeWidth="1.5" />)}
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center">
        <div className="text-2xl font-bold tabular" style={{ color }}>{score == null ? "—" : Number(score).toFixed(4)}</div>
        <div className="text-[11px] text-muted-foreground">điểm rủi ro (xác suất)</div>
      </div>
    </div>
  );
}

export function StatCard({ title, value, sub, icon: Icon, tone, delay = 0, trend }) {
  const toneCls = { HIGH: "text-high bg-high/10", MEDIUM: "text-medium bg-medium/10", LOW: "text-low bg-low/10",
    UNSCORED: "text-unscored bg-unscored/10", CRITICAL: "text-critical bg-critical/10" }[tone] || "text-primary bg-primary/10";
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, delay }}>
      <Card className="relative h-full overflow-hidden p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">{title}</p>
            <p className={cn("whitespace-nowrap font-bold tracking-tight tabular", String(value).length > 9 ? "text-lg" : "text-2xl")}>{value}</p>
          </div>
          {Icon && <div className={cn("rounded-lg p-2", toneCls)}><Icon className="size-4" /></div>}
        </div>
        {(sub || trend) && (
          <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
            {trend === "up" && <TrendingUp className="size-3 text-high" />}{trend === "down" && <TrendingDown className="size-3 text-low" />}
            {sub}
          </p>
        )}
      </Card>
    </motion.div>
  );
}

export function FactorList({ factors }) {
  if (!factors?.length) return <p className="text-xs text-muted-foreground">Chưa có giải thích (brief chưa soạn hoặc giao dịch không có hồ sơ).</p>;
  return (
    <ul className="space-y-1.5">
      {factors.map((f, i) => {
        const up = String(f.direction || "").startsWith("tăng");
        return (
          <li key={i} className="flex items-center justify-between gap-3 rounded-md border bg-muted/30 px-2.5 py-1.5 text-xs">
            <span>{FEATURE_LABELS[f.feature] || f.feature} = <b className="tabular">{typeof f.value === "number" ? num(f.value, 2) : String(f.value)}</b></span>
            <span className={cn("flex shrink-0 items-center gap-1 whitespace-nowrap font-medium", up ? "text-high" : "text-low")}>
              {up ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}{f.direction}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function PageHeader({ title, description, children }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}
