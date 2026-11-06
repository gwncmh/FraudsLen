import React, { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity, AlertTriangle, ArrowRight, BellRing, ChevronDown, CircleGauge, ClipboardCheck, FileClock, Gauge, ListChecks,
  RefreshCw, ScanSearch, ShieldAlert, Sparkles, Target, Timer, Zap,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeaderRow } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, Empty, Progress, Skeleton } from "@/components/ui/misc";
import {
  CaseStatusBadge, FactorList, PageHeader, RiskScoreBar, SeverityBadge, SeverityRail, StatCard, sevColor,
} from "@/components/fraud";
import { usePoll } from "@/lib/hooks";
import { FEATURE_LABELS, ago, can, cn, fmtDate, fmtTs, money, num, pct, severityOf } from "@/lib/utils";

const ROUTES = ["HIGH", "MEDIUM", "UNSCORED", "LOW"];
const tooltipStyle = {
  contentStyle: { background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "hsl(var(--muted-foreground))" },
};

function SetupBanner() {
  const [s, setS] = useState(null);
  usePoll(() => api.system().then(setS).catch(() => {}), 60000);
  const todo = (s?.steps || []).filter((x) => !x.done && !x.optional);
  if (!todo.length) return null;
  return (
    <Alert variant="warn" icon={AlertTriangle} title={`Hệ thống chưa sẵn sàng – còn ${todo.length} bước`} className="mb-6">
      {todo.map((t) => t.title).join(" · ")}. <a href="#/settings/system" className="font-medium text-primary">Mở checklist cài đặt →</a>
    </Alert>
  );
}

// ------------------------------------------------------------------ biểu đồ
function VolumeChart({ data, win, setWin }) {
  const rows = useMemo(() => {
    const m = new Map();
    for (const r of data?.series || []) {
      const k = Number(r.ts);
      if (!m.has(k)) m.set(k, { ts: k, HIGH: 0, MEDIUM: 0, UNSCORED: 0, LOW: 0 });
      m.get(k)[r.route] = Number(r.n);
    }
    return [...m.values()].sort((a, b) => a.ts - b.ts);
  }, [data]);
  const label = (ts) => win === "live"
    ? new Date(ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
    : new Date(ts * 1000).toISOString().slice(5, 13).replace("T", " ") + "h";
  return (
    <Card className="lg:col-span-2">
      <CardHeaderRow icon={Activity} title="Lưu lượng giao dịch theo luồng"
        description={win === "live" ? "60 phút gần nhất, theo giờ hệ thống nhận" : "7 ngày cuối của luồng, theo thời gian giao dịch"}
        actions={
          <Tabs value={win} onValueChange={setWin}>
            <TabsList className="h-8"><TabsTrigger value="live" className="text-xs">Trực tiếp</TabsTrigger><TabsTrigger value="stream" className="text-xs">Theo luồng</TabsTrigger></TabsList>
          </Tabs>} />
      <CardContent>
        {rows.length === 0 ? <Empty icon={Activity} title="Chưa có giao dịch trong khoảng này">Chạy simulator để xem lưu lượng trực tiếp.</Empty> : (
          <div className="h-[260px]">
            <ResponsiveContainer>
              <AreaChart data={rows} margin={{ left: -18, right: 6, top: 8 }}>
                <defs>{ROUTES.map((r) => (
                  <linearGradient key={r} id={`g-${r}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={sevColor(r)} stopOpacity={0.45} /><stop offset="100%" stopColor={sevColor(r)} stopOpacity={0.02} />
                  </linearGradient>))}</defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis dataKey="ts" tickFormatter={label} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} minTickGap={24} />
                <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} allowDecimals={false} />
                <Tooltip {...tooltipStyle} labelFormatter={label} />
                {[...ROUTES].reverse().map((r) => (
                  <Area key={r} type="monotone" dataKey={r} stackId="1" stroke={sevColor(r)} fill={`url(#g-${r})`} strokeWidth={1.5} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted-foreground">
          {ROUTES.map((r) => <span key={r} className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: sevColor(r) }} />{r}</span>)}
        </div>
      </CardContent>
    </Card>
  );
}

function RiskAssessment({ data }) {
  const hist = (data?.score_hist || []).map((b) => ({ ...b, label: b.lo < 1e-4 ? "<0,0001" : `≥${b.lo}` }));
  const k = data?.kpi || {};
  const total = Number(k.total || 0);
  const routesTotal = { HIGH: k.high, FLAG: k.flagged, UNSCORED: k.unscored };
  return (
    <Card>
      <CardHeaderRow icon={CircleGauge} title="Đánh giá rủi ro" description="Phân bố điểm 5.000 giao dịch đã chấm gần nhất" />
      <CardContent className="space-y-5">
        <div className="h-[150px]">
          {hist.some((h) => h.n) ? (
            <ResponsiveContainer>
              <BarChart data={hist} margin={{ left: -24, right: 0, top: 4 }}>
                <XAxis dataKey="label" tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} interval={0} angle={-30} dy={6} height={36} />
                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} scale="sqrt" />
                <Tooltip {...tooltipStyle} formatter={(v) => [num(v), "giao dịch"]} />
                <Bar dataKey="n" radius={[4, 4, 0, 0]}>
                  {hist.map((h, i) => <Cell key={i} fill={h.lo >= 0.03 ? sevColor("HIGH") : h.lo >= 0.003 ? sevColor("MEDIUM") : sevColor("LOW")} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty className="py-6">Chưa có điểm.</Empty>}
        </div>
        <div className="space-y-3">
          {[
            ["Tỉ lệ cần review (MEDIUM+HIGH)", routesTotal.FLAG, "MEDIUM"],
            ["Tỉ lệ HIGH", routesTotal.HIGH, "HIGH"],
            ["Tỉ lệ UNSCORED (fail-safe)", routesTotal.UNSCORED, "UNSCORED"],
          ].map(([l, v, tone]) => (
            <div key={l} className="space-y-1">
              <div className="flex justify-between text-xs"><span className="text-muted-foreground">{l}</span><b className="tabular">{total ? pct(v / total, 2) : "–"}</b></div>
              <Progress value={Number(v || 0)} max={Math.max(total * 0.05, Number(v || 0), 1)} indicatorClassName={`bg-${tone.toLowerCase()}`} />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ flagged transactions
const KEY_FEATURES = ["amt_ratio_30d", "n_txn_1h", "n_txn_24h", "distance_km", "is_new_merchant_30d", "is_night", "secs_since_last"];

function FlaggedItem({ f, open, onToggle }) {
  const sev = severityOf(f.route, f.score);
  const feats = f.features || {};
  const signals = (f.risk_signals || []).map((s) => s.signal || s).filter(Boolean);
  return (
    <motion.div layout className="relative overflow-hidden rounded-xl border bg-card">
      <SeverityRail level={sev} />
      <button onClick={onToggle} className="flex w-full items-center gap-3 py-3 pl-4 pr-3 text-left hover:bg-muted/40">
        <SeverityBadge level={sev} className="w-[78px] justify-center" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold tabular">{money(f.amt)}</span>
            <span className="truncate text-muted-foreground">{f.merchant?.replace(/^fraud_/, "") || "—"}</span>
          </div>
          <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            <span>{f.category || "—"}</span><span>{f.card_last4 ? `•••• ${f.card_last4}` : "thẻ ?"}</span><span>{fmtTs(f.event_ts)}</span>
            <span>Hồ sơ #{f.case_id}</span>
          </div>
        </div>
        <div className="hidden w-32 sm:block"><RiskScoreBar score={f.score} tMed={f.t_medium} tHigh={f.t_high} compact /></div>
        <span className="hidden w-14 text-right text-xs font-semibold tabular sm:block">{f.score != null ? Number(f.score).toFixed(3) : "—"}</span>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.22 }}>
            <div className="grid gap-4 border-t bg-muted/20 p-4 pl-5 md:grid-cols-3">
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tín hiệu rủi ro</p>
                {f.validation_errors?.length ? (
                  <div className="flex flex-wrap gap-1">{f.validation_errors.map((e) => <Badge key={e} variant="UNSCORED">{e}</Badge>)}</div>
                ) : f.score_error ? <Badge variant="UNSCORED">{f.score_status}: {f.score_error}</Badge> : null}
                {signals.length ? <ul className="list-disc space-y-1 pl-4 text-xs">{signals.slice(0, 4).map((s, i) => <li key={i}>{s}</li>)}</ul>
                  : !f.validation_errors?.length && <p className="text-xs text-muted-foreground">{f.brief_source ? "Brief không nêu tín hiệu." : "Brief đang được soạn…"}</p>}
              </div>
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Yếu tố ảnh hưởng</p>
                <FactorList factors={(f.factors || []).slice(0, 4)} />
              </div>
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Hành vi thẻ</p>
                <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
                  {KEY_FEATURES.filter((k) => feats[k] != null).map((k) => (
                    <React.Fragment key={k}><dt className="text-muted-foreground">{FEATURE_LABELS[k]}</dt><dd className="text-right font-medium tabular">{num(feats[k], 2)}</dd></React.Fragment>
                  ))}
                </dl>
                <div className="flex flex-wrap items-center gap-2 pt-2">
                  <CaseStatusBadge status={f.case_status} />
                  <Button size="sm" asChild><a href={`#/review/${f.case_id}`}>Xử lý<ArrowRight /></a></Button>
                  <Button size="sm" variant="outline" asChild><a href={`#/transactions?id=${f.txn_id}`}>Chi tiết GD</a></Button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Flagged({ items }) {
  const [open, setOpen] = useState(null);
  return (
    <Card className="lg:col-span-2">
      <CardHeaderRow icon={ShieldAlert} title="Giao dịch bị gắn cờ" description="15 hồ sơ mới nhất – bấm để xem chi tiết điều tra"
        actions={<Button size="sm" variant="outline" asChild><a href="#/review">Hàng đợi<ArrowRight /></a></Button>} />
      <CardContent className="space-y-2">
        {!items?.length ? <Empty icon={ShieldAlert} title="Chưa có giao dịch bị gắn cờ" /> :
          items.map((f) => <FlaggedItem key={f.case_id} f={f} open={open === f.case_id} onToggle={() => setOpen(open === f.case_id ? null : f.case_id)} />)}
      </CardContent>
    </Card>
  );
}

function RuleTriggers({ t }) {
  const groups = [
    { title: "Yếu tố đẩy rủi ro (brief gần đây)", icon: Target, rows: (t?.factors || []).map((r) => ({ k: FEATURE_LABELS[r.feature] || r.feature, n: r.n })), tone: "HIGH" },
    { title: "Lỗi kiểm tra dữ liệu", icon: ScanSearch, rows: (t?.validation || []).map((r) => ({ k: r.code, n: r.n })), tone: "UNSCORED" },
    { title: "Lỗi chấm điểm / lịch sử", icon: Zap, rows: (t?.scoring || []).map((r) => ({ k: `${r.code || "?"}`, n: r.n })), tone: "MEDIUM" },
  ];
  return (
    <Card>
      <CardHeaderRow icon={ListChecks} title="Rule trigger" description="Tín hiệu kích hoạt nhiều nhất" />
      <CardContent className="space-y-5">
        {groups.map((g) => {
          const max = Math.max(1, ...g.rows.map((r) => r.n));
          return (
            <div key={g.title} className="space-y-2">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"><g.icon className="size-3.5" />{g.title}</p>
              {g.rows.length === 0 ? <p className="text-xs text-muted-foreground">Không có.</p> : g.rows.slice(0, 5).map((r) => (
                <div key={r.k} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 text-xs">
                  <span className="truncate">{r.k}</span><span className="font-semibold tabular">{num(r.n)}</span>
                  <Progress value={r.n} max={max} className="col-span-2 h-1" indicatorClassName={`bg-${g.tone.toLowerCase()}`} />
                </div>
              ))}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function SecurityOverview({ data }) {
  const [sys, setSys] = useState(null);
  usePoll(() => api.system().then(setSys).catch(() => {}), 30000);
  const alerts = (data?.monitor || []).filter((m) => m.level && m.level !== "OK");
  const svc = sys?.services || {};
  return (
    <Card>
      <CardHeaderRow icon={BellRing} title="Tổng quan an toàn" description="Dịch vụ, drift và cảnh báo chất lượng"
        actions={<Badge variant={alerts.length ? "HIGH" : "LOW"}>{alerts.length ? `${alerts.length} cảnh báo` : "Ổn định"}</Badge>} />
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-2">
          {["postgres", "redis", "api", "scorer", "worker", "monitor"].map((k) => (
            <div key={k} className="flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs">
              <span className={cn("size-2 rounded-full", !sys ? "bg-muted-foreground" : svc[k]?.ok ? "bg-low" : "bg-destructive")} />
              <span className="capitalize">{k}</span>
            </div>
          ))}
        </div>
        {sys?.model && <p className="text-xs text-muted-foreground">Model ACTIVE: <span className="font-mono text-foreground">{sys.model.active || "chưa có"}</span></p>}
        <div className="space-y-2">
          {alerts.length === 0 ? <p className="text-xs text-muted-foreground">Không có cảnh báo drift / chất lượng. Monitor chạy 5 phút/lần.</p> :
            alerts.slice(0, 5).map((m, i) => (
              <div key={i} className="rounded-lg border border-high/30 bg-high/5 p-2.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{m.metric}{m.feature ? ` · ${m.feature}` : ""}</span>
                  <Badge variant={m.level === "WARN" ? "MEDIUM" : "HIGH"}>{m.level} {m.value != null && num(m.value, 3)}</Badge>
                </div>
                {m.action && <p className="mt-1 text-muted-foreground">{m.owner}: {m.action}</p>}
              </div>
            ))}
        </div>
      </CardContent>
    </Card>
  );
}

function AuditTrail({ items, canSee }) {
  return (
    <Card>
      <CardHeaderRow icon={FileClock} title="Audit trail" description="Nhật ký chỉ-thêm, có chuỗi băm"
        actions={canSee && <Button size="sm" variant="ghost" asChild><a href="#/settings/audit">Tất cả<ArrowRight /></a></Button>} />
      <CardContent>
        {!items?.length ? <Empty>Chưa có thao tác.</Empty> : (
          <ol className="relative space-y-3 border-l pl-4">
            {items.slice(0, 8).map((a) => (
              <li key={a.seq} className="relative text-xs">
                <span className="absolute -left-[21px] top-1 size-2.5 rounded-full border-2 border-background bg-primary" />
                <p><span className="font-medium">{a.actor.replace(/^user:/, "")}</span> · <span className="font-mono">{a.action}</span> {a.entity} <span className="text-muted-foreground">#{a.entity_id}</span></p>
                <p className="text-muted-foreground">{ago(a.ts)}</p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function Categories({ items }) {
  const max = Math.max(1, ...(items || []).map((c) => c.n));
  return (
    <Card>
      <CardHeaderRow icon={Sparkles} title="Loại merchant bị gắn cờ nhiều" description="MEDIUM + HIGH, toàn bộ" />
      <CardContent className="space-y-2.5">
        {!items?.length ? <Empty>Chưa có.</Empty> : items.map((c) => (
          <div key={c.category} className="space-y-1 text-xs">
            <div className="flex justify-between"><span>{c.category}</span><span className="text-muted-foreground tabular">{num(c.n)} GD · {money(c.amount)}</span></div>
            <Progress value={c.n} max={max} className="h-1.5" indicatorClassName="bg-high" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export default function Dashboard({ user }) {
  const [win, setWin] = useState("live");
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [updated, setUpdated] = useState(null);
  const reload = usePoll(() => api.dashboard(win).then((x) => { setD(x); setErr(""); setUpdated(new Date()); }).catch((e) => setErr(e.message)), 10000, [win]);

  const k = d?.kpi || {};
  const open = k.open_cases || {};
  const lh = k.last_hour || {};
  const lab = k.labels || {};
  const total = Number(k.total || 0);
  return (
    <>
      <PageHeader title="Dashboard giám sát gian lận" description={updated ? `Cập nhật ${updated.toLocaleTimeString("vi-VN")} · tự làm mới mỗi 10 giây` : "Đang tải…"}>
        <Button variant="outline" size="sm" onClick={reload}><RefreshCw />Làm mới</Button>
        <Button size="sm" asChild><a href="#/review"><ClipboardCheck />Xử lý hồ sơ</a></Button>
      </PageHeader>
      <SetupBanner />
      {err && <Alert variant="error" title="Không tải được dashboard" className="mb-6">{err}</Alert>}
      {!d ? (
        <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>
      ) : (<>
        <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
          <StatCard delay={0} title="Giao dịch đã xử lý" value={num(total)} icon={Activity} sub={`${num(lh.n)} trong 60 phút qua`} />
          <StatCard delay={0.04} title="HIGH đang mở" value={num(open.HIGH || 0)} icon={ShieldAlert} tone="HIGH" sub={`${num(k.escalated)} hồ sơ đã chuyển cấp`} />
          <StatCard delay={0.08} title="MEDIUM đang mở" value={num(open.MEDIUM || 0)} icon={AlertTriangle} tone="MEDIUM" sub={`UNSCORED mở: ${num(open.UNSCORED || 0)}`} />
          <StatCard delay={0.12} title="Tỉ lệ gắn cờ" value={total ? pct(k.flagged / total, 2) : "–"} icon={Gauge} tone="UNSCORED" sub={`${num(k.flagged)} giao dịch MEDIUM/HIGH`} />
          <StatCard delay={0.16} title="Độ trễ p95 (60 phút)" value={lh.p95_latency_ms != null ? `${num(lh.p95_latency_ms, 0)} ms` : "–"} icon={Timer}
            tone={lh.p95_latency_ms > 500 ? "HIGH" : "LOW"} sub={`mục tiêu ≤ 500 ms · TB ${num(lh.avg_latency_ms, 1)} ms`} />
          <StatCard delay={0.2} title="Recall (nhãn trễ)" value={lab.frauds ? pct(lab.caught / lab.frauds) : "–"} icon={Target} tone="LOW"
            sub={lab.frauds ? `${num(lab.caught)}/${num(lab.frauds)} ca · precision ${pct(lab.caught / Math.max(1, lab.flagged_labeled))}` : "chưa có nhãn"} />
        </div>
        <div className="mb-6 grid gap-6 lg:grid-cols-3">
          <VolumeChart data={d} win={win} setWin={setWin} />
          <RiskAssessment data={d} />
        </div>
        <div className="mb-6 grid gap-6 lg:grid-cols-3">
          <Flagged items={d.flagged} />
          <RuleTriggers t={d.triggers} />
        </div>
        <div className="grid gap-6 lg:grid-cols-3">
          <SecurityOverview data={d} />
          <Categories items={d.categories} />
          <AuditTrail items={d.audit} canSee={can(user, "risk_lead", "admin")} />
        </div>
      </>)}
    </>
  );
}
