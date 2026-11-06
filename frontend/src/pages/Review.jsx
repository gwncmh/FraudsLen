import React, { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUpRight, BadgeCheck, BrainCircuit, ChevronDown, ClipboardCheck, CreditCard, FileClock, Gauge, History, Inbox,
  Loader2, MessageSquarePlus, RefreshCw, Search, ShieldX, ThumbsDown, ThumbsUp, TriangleAlert,
} from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, DataList, Empty, Skeleton } from "@/components/ui/misc";
import { useToast } from "@/components/ui/toast";
import {
  CaseStatusBadge, FactorList, PageHeader, RiskGauge, RiskScoreBar, SeverityBadge, SeverityRail,
} from "@/components/fraud";
import { navigate, usePoll } from "@/lib/hooks";
import { FEATURE_LABELS, can, cn, fmtDate, fmtTs, money, num, severityOf } from "@/lib/utils";

const VIEWS = {
  open: { label: "Đang mở", params: { status: "open" } },
  HIGH: { label: "HIGH", params: { status: "open", route: "HIGH" } },
  MEDIUM: { label: "MEDIUM", params: { status: "open", route: "MEDIUM" } },
  UNSCORED: { label: "UNSCORED", params: { status: "open", route: "UNSCORED" } },
  escalated: { label: "Chuyển cấp", params: { status: "open", assigned_role: "risk_lead" } },
  closed: { label: "Đã đóng", params: { status: "closed" } },
};
const ACTION_LABELS = {
  NOTE: "Ghi nhận định", INVESTIGATE: "Điều tra thêm", ESCALATE: "Chuyển cấp", CLOSE_FRAUD: "Đóng: gian lận",
  CLOSE_LEGIT: "Đóng: hợp lệ", REGENERATE_BRIEF: "Soạn lại brief", BRIEF_USEFUL: "Brief hữu ích", BRIEF_NOT_USEFUL: "Brief không hữu ích",
};

// ------------------------------------------------------------------ hàng đợi
function QueueList({ items, selected, onSelect }) {
  if (!items) return <div className="space-y-2 p-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>;
  if (!items.length) return <Empty icon={Inbox} title="Hàng đợi trống">Không có hồ sơ phù hợp.</Empty>;
  return (
    <div className="space-y-1.5 p-2">
      {items.map((c) => {
        const sev = severityOf(c.route, c.score);
        return (
          <button key={c.id} onClick={() => onSelect(c.id)}
            className={cn("relative w-full overflow-hidden rounded-lg border bg-card py-2.5 pl-4 pr-3 text-left transition-colors hover:bg-muted/50",
              selected === c.id && "border-primary/50 bg-primary/5 ring-1 ring-primary/30")}>
            <SeverityRail level={sev} />
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">#{c.id} <span className="font-normal tabular">{money(c.amt)}</span></span>
              <SeverityBadge level={sev} />
            </div>
            <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span className="truncate">{c.category || "—"} · {c.card_last4 ? `•••• ${c.card_last4}` : "—"}</span>
              <span className="tabular">{c.score != null ? Number(c.score).toFixed(3) : "—"}</span>
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <CaseStatusBadge status={c.status} />
              <span className="text-[11px] text-muted-foreground">{c.brief_source === "llm" ? "Brief LLM" : c.brief_source === "fallback" ? "Brief mẫu" : "Đang soạn brief"}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ chi tiết hồ sơ
function Section({ icon: Icon, title, badge, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card className="overflow-hidden">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-muted/40">
        <Icon className="size-4 text-primary" /><span className="flex-1 text-sm font-semibold">{title}</span>{badge}
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }}>
            <div className="border-t px-4 py-4">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

function Brief({ d, onFeedback }) {
  if (d.brief_holdout) return <p className="text-sm text-muted-foreground">Hồ sơ thuộc nhóm đối chứng: brief không hiển thị (dùng để đo giá trị của brief).</p>;
  const brief = d.brief;
  if (!brief) return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Brief đang được soạn{d.brief_job?.last_error ? ` (lỗi gần nhất: ${d.brief_job.last_error})` : ""}…</p>;
  const b = brief.brief;
  const rej = brief.guardrail?.rejections || [];
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {brief.brief_source === "llm" ? <Badge variant="info"><BrainCircuit />LLM · {brief.llm_model}</Badge> : <Badge variant="secondary">Brief dạng mẫu</Badge>}
        <span>playbook {brief.kb_version}</span><span>· đoạn: {(brief.chunk_ids || []).join(", ") || "–"}</span>
      </div>
      {brief.brief_source === "fallback" && rej.length > 0 && (
        <Alert variant="warn" icon={TriangleAlert} title="Brief LLM bị bộ kiểm tra loại">{rej.map((r) => (typeof r === "string" ? r : JSON.stringify(r))).join("; ")}</Alert>
      )}
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tóm tắt</p>
        <ul className="list-disc space-y-1 pl-5">{b.summary.map((s, i) => <li key={i}>{s.text}</li>)}</ul></div>
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tín hiệu rủi ro</p>
        {b.risk_signals.length ? <ul className="space-y-1">{b.risk_signals.map((s, i) => (
          <li key={i} className="flex gap-2"><TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-high" />{s.signal}</li>))}</ul>
          : <p className="text-muted-foreground">Không có.</p>}</div>
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Các bước kiểm tra</p>
        <ol className="space-y-1.5">{b.checklist.map((c, i) => (
          <li key={i} className="flex gap-2"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{i + 1}</span>
            <span>{c.step} <span className="text-xs text-muted-foreground">[{c.source}]</span></span></li>))}</ol></div>
      {b.limitations?.length > 0 && <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Giới hạn</p>
        <ul className="list-disc space-y-1 pl-5 text-muted-foreground">{b.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></div>}
      <p className="border-t pt-2 text-xs italic text-muted-foreground">{b.disclaimer}</p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Brief có hữu ích không?</span>
        <Button size="sm" variant="outline" onClick={() => onFeedback("BRIEF_USEFUL", brief.id)}><ThumbsUp />Hữu ích</Button>
        <Button size="sm" variant="outline" onClick={() => onFeedback("BRIEF_NOT_USEFUL", brief.id)}><ThumbsDown />Không</Button>
      </div>
    </div>
  );
}

function CaseDetail({ id, user, onChanged, onNext }) {
  const [d, setD] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const toast = useToast();
  const canAct = can(user, "analyst", "risk_lead");

  const load = usePoll(() => api.caseDetail(id).then((x) => { setD(x); setErr(""); }).catch((e) => setErr(e.message)), 10000, [id]);
  useEffect(() => { setD(null); setNote(""); if (canAct) api.act(id, "VIEW", null).catch(() => {}); }, [id]);

  const act = async (action, goNext) => {
    if (action.startsWith("CLOSE") && !window.confirm(`${ACTION_LABELS[action]} hồ sơ #${id}?`)) return;
    setBusy(action);
    try {
      await api.act(id, action, note); setNote(""); toast(`#${id}: ${ACTION_LABELS[action]}`); onChanged();
      if (goNext) onNext(); else load();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };
  const feedback = async (action, briefId) => {
    try { await api.act(id, action, `brief_id=${briefId}`); toast("Đã ghi nhận đánh giá brief"); load(); } catch (e) { toast(e.message, "error"); }
  };

  if (!d) return err ? <Alert variant="error" title="Không tải được hồ sơ">{err}</Alert> : <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-64" /></div>;
  const c = d.case;
  const f = c.features || {};
  const sev = severityOf(c.route, c.score);
  const closed = c.status.startsWith("CLOSED");
  const escalatedLock = c.status === "ESCALATED" && user.role !== "risk_lead";
  const history = d.actions.filter((a) => a.action !== "VIEW");

  return (
    <div className="space-y-4">
      <Card className="relative overflow-hidden p-5">
        <SeverityRail level={sev} />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-bold">Hồ sơ #{c.id}</h2><SeverityBadge level={sev} /><CaseStatusBadge status={c.status} />
              {c.assigned_role === "risk_lead" && <Badge variant="HIGH">Risk Lead phụ trách</Badge>}
            </div>
            <p className="text-3xl font-bold tabular">{money(c.amt)}</p>
            <p className="text-sm text-muted-foreground">{c.merchant?.replace(/^fraud_/, "") || "—"} · {c.category} · {c.card_last4 ? `thẻ •••• ${c.card_last4}` : "thẻ ?"} · {fmtTs(c.event_ts)}</p>
            <p className="font-mono text-xs text-muted-foreground">{c.trans_num} · tạo {fmtDate(c.created_at)}</p>
          </div>
          <RiskGauge score={c.score} tMed={c.t_medium} tHigh={c.t_high} />
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-5">
        <div className="space-y-4 xl:col-span-3">
          <Section icon={BrainCircuit} title="Case Brief" badge={d.brief && <Badge variant={d.brief.brief_source === "llm" ? "info" : "secondary"}>{d.brief.brief_source}</Badge>}>
            <Brief d={d} onFeedback={feedback} />
          </Section>
          <Section icon={Gauge} title="Điểm và giải thích">
            <div className="space-y-4">
              <RiskScoreBar score={c.score} tMed={c.t_medium} tHigh={c.t_high} />
              <DataList items={[
                ["Trạng thái chấm", `${c.score_status}${c.score_error ? ` (${c.score_error})` : ""}`],
                ["Ngưỡng", `MEDIUM ≥ ${num(c.t_medium, 5)} · HIGH ≥ ${num(c.t_high, 5)}`],
                ["Model / config", <span key="m" className="font-mono text-xs">{c.model_version || "–"} · {c.config_version || "–"}</span>],
                c.validation_errors?.length ? ["Lỗi dữ liệu", <span key="v" className="flex flex-wrap gap-1">{c.validation_errors.map((e) => <Badge key={e} variant="UNSCORED">{e}</Badge>)}</span>] : null,
              ]} />
              <FactorList factors={d.brief?.factors} />
            </div>
          </Section>
          <Section icon={CreditCard} title="Hành vi thẻ (feature)" defaultOpen={false}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {Object.keys(FEATURE_LABELS).filter((k) => f[k] != null).map((k) => (
                <div key={k} className="rounded-md border px-2.5 py-1.5 text-xs">
                  <p className="text-muted-foreground">{FEATURE_LABELS[k]}</p><p className="font-semibold tabular">{typeof f[k] === "number" ? num(f[k], 2) : String(f[k])}</p>
                </div>
              ))}
            </div>
          </Section>
        </div>

        <div className="space-y-4 xl:col-span-2">
          <Card className="p-4 xl:sticky xl:top-20">
            <p className="mb-3 flex items-center gap-2 text-sm font-semibold"><ClipboardCheck className="size-4 text-primary" />Quyết định</p>
            {!canAct ? <p className="text-sm text-muted-foreground">Vai trò {user.role} chỉ xem, không xử lý hồ sơ.</p>
              : closed ? <Alert icon={BadgeCheck} title="Hồ sơ đã đóng">Xem lịch sử thao tác bên dưới.</Alert> : (
              <div className="space-y-3">
                <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nhận định của chuyên viên (bắt buộc khi đóng hồ sơ)…" rows={4} />
                <div className="grid grid-cols-2 gap-2">
                  <Button variant="destructive" disabled={!note.trim() || escalatedLock || !!busy} onClick={() => act("CLOSE_FRAUD", true)}>
                    {busy === "CLOSE_FRAUD" ? <Loader2 className="animate-spin" /> : <ShieldX />}Gian lận
                  </Button>
                  <Button variant="success" disabled={!note.trim() || escalatedLock || !!busy} onClick={() => act("CLOSE_LEGIT", true)}>
                    {busy === "CLOSE_LEGIT" ? <Loader2 className="animate-spin" /> : <BadgeCheck />}Hợp lệ
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button variant="outline" size="sm" disabled={!!busy || c.status === "INVESTIGATING"} onClick={() => act("INVESTIGATE")}><Search />Điều tra thêm</Button>
                  <Button variant="outline" size="sm" disabled={!!busy || c.status === "ESCALATED"} onClick={() => act("ESCALATE")}><ArrowUpRight />Chuyển cấp</Button>
                  <Button variant="outline" size="sm" disabled={!note.trim() || !!busy} onClick={() => act("NOTE")}><MessageSquarePlus />Ghi nhận định</Button>
                  <Button variant="outline" size="sm" disabled={!!busy} onClick={() => act("REGENERATE_BRIEF")}><RefreshCw />Soạn lại brief</Button>
                </div>
                {escalatedLock && <p className="text-xs text-muted-foreground">Hồ sơ đã chuyển cấp: chỉ Risk Lead được đóng.</p>}
                <p className="text-[11px] text-muted-foreground">Đóng hồ sơ sẽ tự chuyển sang hồ sơ tiếp theo trong hàng đợi.</p>
              </div>
            )}
          </Card>
          <Section icon={History} title="Lịch sử thao tác" badge={<Badge variant="secondary">{history.length}</Badge>}>
            {!history.length ? <p className="text-sm text-muted-foreground">Chưa có thao tác.</p> : (
              <ol className="relative space-y-3 border-l pl-4">
                {history.map((a, i) => (
                  <li key={i} className="relative text-sm">
                    <span className="absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-background bg-primary" />
                    <p><b>{a.actor}</b> <span className="text-muted-foreground">({a.role})</span> · {ACTION_LABELS[a.action] || a.action}</p>
                    {a.note && <p className="text-muted-foreground">“{a.note}”</p>}
                    <p className="text-xs text-muted-foreground">{fmtDate(a.created_at)}</p>
                  </li>
                ))}
              </ol>
            )}
          </Section>
          <Button variant="ghost" size="sm" asChild className="w-full"><a href={`#/transactions?q=${encodeURIComponent(c.trans_num || "")}`}><FileClock />Xem giao dịch trong danh sách</a></Button>
        </div>
      </div>
    </div>
  );
}

export default function Review({ user, caseId }) {
  const [view, setView] = useState("open");
  const [items, setItems] = useState(null);
  const [q, setQ] = useState("");
  const [counts, setCounts] = useState({});

  const reload = usePoll(() => {
    api.cases({ ...VIEWS[view].params, limit: 200 }).then((d) => setItems(d.items)).catch(() => setItems([]));
    api.stats().then((s) => setCounts(Object.fromEntries(s.open_cases.map((r) => [r.route, r.n])))).catch(() => {});
  }, 15000, [view]);

  const needle = q.trim().toLowerCase();
  const shown = (items || []).filter((c) => !needle || [c.id, c.trans_num, c.card_last4, c.category, c.merchant].some((v) => String(v ?? "").toLowerCase().includes(needle)));
  const select = (id) => navigate(`/review/${id}`);
  const next = () => {
    const i = shown.findIndex((c) => c.id === caseId);
    const n = shown[i + 1] || shown.find((c) => c.id !== caseId);
    if (n) select(n.id); else navigate("/review");
  };
  // chọn hồ sơ đầu tiên khi chưa chọn
  useEffect(() => { if (!caseId && shown.length && window.innerWidth >= 1024) select(shown[0].id); }, [caseId, items]);

  return (
    <>
      <PageHeader title="Xử lý thủ công" description="Review hồ sơ theo thứ tự HIGH → MEDIUM → UNSCORED, trong mỗi mức theo điểm rồi số tiền.">
        <Button variant="outline" size="sm" onClick={reload}><RefreshCw />Làm mới</Button>
      </PageHeader>
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card className="flex max-h-[calc(100vh-170px)] flex-col overflow-hidden lg:sticky lg:top-20">
          <div className="space-y-2 border-b p-3">
            <Tabs value={view} onValueChange={setView}>
              <TabsList className="h-auto w-full flex-wrap justify-start">
                {Object.entries(VIEWS).map(([k, v]) => (
                  <TabsTrigger key={k} value={k} className="text-xs">{v.label}{counts[k] ? <span className="ml-1 rounded bg-muted px-1 tabular">{counts[k]}</span> : null}</TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Lọc: mã hồ sơ, thẻ, merchant…" className="pl-8" />
            </div>
            <p className="text-xs text-muted-foreground">{num(shown.length)} hồ sơ{items?.length === 200 ? " (200 đầu tiên)" : ""}</p>
          </div>
          <div className="flex-1 overflow-y-auto"><QueueList items={items && shown} selected={caseId} onSelect={select} /></div>
        </Card>
        <div className="min-w-0">
          {caseId ? <CaseDetail key={caseId} id={caseId} user={user} onChanged={reload} onNext={next} />
            : <Card><Empty icon={ClipboardCheck} title="Chọn một hồ sơ để bắt đầu review">Hồ sơ được tạo tự động cho giao dịch MEDIUM, HIGH và UNSCORED.</Empty></Card>}
        </div>
      </div>
    </>
  );
}
