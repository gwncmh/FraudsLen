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

const BASELINE_NAMES = { model: "XGBoost (model này)", rules: "Luật viết tay", lr: "Logistic Regression", tree: "Decision Tree" };

/** Kết quả trên test set (fraudTest.csv) – chỉ có sau khi chạy `python -m fraudlens.ml.final_test` MỘT lần cho model đã duyệt. */
function TestResults({ m }) {
  const t = m.test;
  if (!t) {
    return m.active && m.approved_by ? (  // chỉ nhắc ở model đang dùng: test set dành cho model đã chốt
      <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
        Chưa chạy test set. Số đo ở trên là val_B (dùng để chọn và duyệt model). Khi đã chốt model, chạy <b>một lần</b>:{" "}
        <code className="break-all">docker compose --profile tools run --rm --entrypoint python trainer -m fraudlens.ml.final_test --data-dir /app/data --version {m.version}</code>
      </p>
    ) : null;
  }
  if (t.error) return <Alert variant="error" title="Kết quả test set">{t.error}</Alert>;
  const rows = Object.entries(t.comparison || {});
  const cm = t.at_t_medium;
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <p className="text-sm font-semibold">Test set <span className="text-xs font-normal text-muted-foreground">
        · {num(t.protocol?.test_rows)} GD, {num(t.protocol?.test_frauds)} gian lận · ngưỡng {t.protocol?.thresholds_from}{t.run_at ? ` · chạy ${fmtDate(t.run_at)}` : ""}</span></p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs tabular">
          <thead><tr className="text-left text-muted-foreground"><th className="py-1 pr-3 font-medium">Mô hình</th><th className="pr-3 font-medium">Recall@1%</th><th className="pr-3 font-medium">Precision@1%</th><th className="pr-3 font-medium">FP@1%</th><th className="pr-3 font-medium">Bỏ sót@1%</th><th className="pr-3 font-medium">PR-AUC</th><th className="font-medium">F1 @t_medium</th></tr></thead>
          <tbody>{rows.map(([k, r]) => (
            <tr key={k} className={cn("border-t", k === "model" && "font-semibold")}>
              <td className="py-1 pr-3">{BASELINE_NAMES[k] || k}</td><td className="pr-3">{pct(r["recall@1%"], 2)}</td><td className="pr-3">{pct(r["precision@1%"], 1)}</td>
              <td className="pr-3">{num(r["fp@1%"])}</td><td className="pr-3">{num(r["missed@1%"])}</td><td className="pr-3">{num(r.pr_auc, 3)}</td><td>{num(r["f1@t_medium"], 3)}</td>
            </tr>))}</tbody>
        </table>
      </div>
      {cm && <p className="text-xs text-muted-foreground">Confusion matrix tại t_medium: TP {num(cm.tp)} · FP {num(cm.fp)} · FN {num(cm.fn)} · TN {num(cm.tn)} · precision {pct(cm.precision, 1)} · recall {pct(cm.recall, 1)}
        {t.routing ? ` · workload ${num(t.routing.workload_ratio, 2)}× năng lực review` : ""}</p>}
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
          <TestResults m={m} />
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
