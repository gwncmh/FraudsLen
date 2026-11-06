import React, { useState } from "react";
import { motion } from "framer-motion";
import { Boxes, CheckCircle2, Loader2, RotateCcw, ShieldAlert, ShieldCheck } from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeaderRow } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Alert, Empty, Skeleton } from "@/components/ui/misc";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/fraud";
import { usePoll } from "@/lib/hooks";
import { can, cn, fmtDate, num, pct } from "@/lib/utils";

const created = (s) => (s && s.length >= 14 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8, 10)}:${s.slice(10, 12)}` : s || "–");

function Metric({ label, value, sub }) {
  return (
    <div className="rounded-lg border bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-bold tabular">{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground">{sub}</p>}
    </div>
  );
}

function ModelCard({ m, user, onApproved, i }) {
  const [override, setOverride] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const gateOk = !m.gate_violations?.length;
  const vb = m.val_b || {}, chk = m.val_b_check || {};

  const approve = async () => {
    if (!window.confirm(`Duyệt ${m.version} làm model ACTIVE? Scorer và API sẽ tự nạp trong vài giây.`)) return;
    setBusy(true);
    try { const r = await api.approve(m.version, override || null); toast(`ACTIVE = ${r.active}`); onApproved(); }
    catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  if (m.error) return <Card className="p-4"><p className="font-mono text-sm">{m.version}</p><p className="text-sm text-destructive">{m.error}</p></Card>;
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
      <Card className={cn(m.active && "border-low/50 ring-1 ring-low/30")}>
        <CardHeaderRow icon={Boxes}
          title={<span className="flex flex-wrap items-center gap-2 font-mono text-sm">{m.version}
            {m.active && <Badge variant="LOW"><CheckCircle2 />ACTIVE</Badge>}
            {gateOk ? <Badge variant="info"><ShieldCheck />gate OK</Badge> : <Badge variant="HIGH"><ShieldAlert />gate chặn</Badge>}</span>}
          description={`Huấn luyện ${created(m.created_at)} · feature set ${m.feature_set_version || "–"}`} />
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Recall@1% (val_B)" value={pct(vb["recall_at_0.01"], 2)}
              sub={Object.entries(m.baselines_recall_at_1pct || {}).map(([k, v]) => `${k} ${pct(v, 1)}`).join(" · ") || "chưa có baseline"} />
            <Metric label="Precision@1% · PR-AUC" value={`${pct(vb["precision_at_0.01"], 1)} · ${num(vb.pr_auc, 3)}`} sub={`FP ${num(vb["fp_at_0.01"])} · bỏ sót ${num(vb["missed_at_0.01"])}`} />
            <Metric label="Ngưỡng" value={<span className="text-sm">M ≥ {num(m.t_medium, 5)} · H ≥ {num(m.t_high, 5)}</span>} sub={`chính sách ${m.policy} · K = ${num(m.K)}`} />
            <Metric label="Workload · recall M+H" value={`${num(chk.workload_ratio, 2)} · ${pct(chk.recall_medium_high, 1)}`} sub={`precision HIGH ${pct(chk.precision_high, 1)}`} />
          </div>
          {!gateOk && <Alert variant="error" icon={ShieldAlert} title="Release gate chặn"><ul className="list-disc pl-4">{m.gate_violations.map((v, k) => <li key={k}>{v}</li>)}</ul></Alert>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {m.approved_by ? <>Duyệt bởi <b className="text-foreground">{m.approved_by}</b> lúc {fmtDate(m.approved_at)}{m.gate_override ? ` (override: ${m.gate_override})` : ""}</> : "Chưa duyệt"}
              {m.has_test_results && " · đã chạy test set"}
            </p>
            {can(user, "risk_lead") && !m.active && (
              <div className="flex flex-1 flex-wrap justify-end gap-2">
                {!gateOk && <Input className="max-w-sm" value={override} onChange={(e) => setOverride(e.target.value)} placeholder="Lý do duyệt dù gate chặn (bắt buộc)" />}
                <Button disabled={busy || (!gateOk && !override.trim())} onClick={approve}>
                  {busy ? <Loader2 className="animate-spin" /> : m.approved_by ? <RotateCcw /> : <ShieldCheck />}{m.approved_by ? "Kích hoạt lại (quay lui)" : "Duyệt và kích hoạt"}
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

export default function Models({ user }) {
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const reload = usePoll(() => api.models().then((d) => { setItems(d.items); setErr(""); }).catch((e) => setErr(e.message)), 30000);
  return (
    <>
      <PageHeader title="Model registry" description="Release gate kiểm tra trên val_B: vượt baseline, workload, recall slice trọng yếu, behavioral test." />
      {!can(user, "risk_lead") && <Alert className="mb-6" title="Chỉ Risk Lead được duyệt model">Admin cấp vai trò ở Cài đặt → Người dùng.</Alert>}
      {err && <Alert variant="error" className="mb-6" title="Lỗi">{err}</Alert>}
      {!items && <div className="space-y-4"><Skeleton className="h-48" /><Skeleton className="h-48" /></div>}
      {items && !items.length && (
        <Card><Empty icon={Boxes} title="Chưa có model trong registry">
          Chạy <code>start.bat</code> (tự huấn luyện trong Docker) hoặc <code>docker compose --profile tools run --rm trainer --sample-cards 0.5</code>, rồi quay lại trang này.
        </Empty></Card>
      )}
      <div className="space-y-4">{(items || []).map((m, i) => <ModelCard key={m.version} i={i} m={m} user={user} onApproved={reload} />)}</div>
    </>
  );
}
