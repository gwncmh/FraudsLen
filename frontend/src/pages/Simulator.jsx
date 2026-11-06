import React, { useState } from "react";
import { motion } from "framer-motion";
import { CheckCircle2, FileWarning, Gauge, History, Loader2, Play, Rocket, Settings2, Square, Timer, XCircle, Zap } from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeaderRow } from "@/components/ui/card";
import { Field, Input, NativeSelect, Switch } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, Empty, Progress, Skeleton } from "@/components/ui/misc";
import { useToast } from "@/components/ui/toast";
import { LiveDot, PageHeader, StatCard, sevColor } from "@/components/fraud";
import { usePoll } from "@/lib/hooks";
import { JOB_STATUS, can, cn, fmtDate, num } from "@/lib/utils";

const PRESETS = [
  { key: "quick", label: "Chạy thử", desc: "2.000 GD · 20 GD/s · ~2 phút", icon: Play, p: { limit: 2000, tps: 20, threads: 4, fault_rate: 0.02 } },
  { key: "more", label: "Chạy tiếp", desc: "20.000 GD · 50 GD/s · ~7 phút", icon: Rocket, p: { limit: 20000, tps: 50, threads: 4, fault_rate: 0.02 } },
  { key: "load", label: "Kiểm thử tải", desc: "20.000 GD · 200 GD/s · không lỗi", icon: Zap, p: { limit: 20000, tps: 200, threads: 16, fault_rate: 0 } },
  { key: "clean", label: "Không tiêm lỗi", desc: "200 GD · 20 GD/s (thử khi tắt scorer)", icon: FileWarning, p: { limit: 200, tps: 20, threads: 4, fault_rate: 0 } },
];
const ROUTES = ["HIGH", "MEDIUM", "LOW", "UNSCORED"];
const jobVariant = (s) => (s === "DONE" ? "LOW" : s === "FAILED" ? "HIGH" : s === "STOPPED" ? "secondary" : "info");

function RouteSplit({ routes }) {
  const total = ROUTES.reduce((a, r) => a + (routes?.[r] || 0), 0);
  return (
    <div className="space-y-2">
      <div className="flex h-3 overflow-hidden rounded-full bg-secondary">
        {ROUTES.map((r) => (routes?.[r] ? (
          <motion.div key={r} title={`${r}: ${routes[r]}`} style={{ background: sevColor(r) }} initial={{ width: 0 }}
            animate={{ width: `${(100 * routes[r]) / Math.max(1, total)}%` }} transition={{ duration: 0.5 }} />) : null))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {ROUTES.map((r) => (
          <span key={r} className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: sevColor(r) }} />
            {r} <b className="tabular">{num(routes?.[r] || 0)}</b></span>))}
      </div>
    </div>
  );
}

function Live({ job, onStop, canRun }) {
  const p = job.progress || {};
  const lat = p.latency_recent_ms || {};
  const elapsed = p.elapsed_s || 0;
  const rate = elapsed ? (p.done || 0) / elapsed : 0;
  const total = p.total || job.params?.limit || 0;
  const left = rate && total ? (total - (p.done || 0)) / rate : null;
  return (
    <Card className="mb-6 border-primary/40 ring-1 ring-primary/20">
      <CardHeaderRow icon={Loader2} title={<span className="flex items-center gap-2"><LiveDot />Đang chạy simulator #{job.id}</span>}
        description={`offset ${num(job.params?.offset)} · ${num(job.params?.tps)} GD/s mục tiêu · ${job.params?.dataset === "train" ? "fraudTrain" : "fraudTest"}.csv`}
        actions={canRun && <Button variant="destructive" size="sm" onClick={onStop} disabled={job.status === "STOPPING"}><Square />{job.status === "STOPPING" ? "Đang dừng…" : "Dừng"}</Button>} />
      <CardContent className="space-y-5">
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs"><span>{num(p.done || 0)} / {num(total)} giao dịch</span><span className="font-semibold tabular">{total ? Math.round((100 * (p.done || 0)) / total) : 0}%</span></div>
          <Progress value={p.done || 0} max={total} className="h-3" />
        </div>
        <div className="grid gap-3 sm:grid-cols-4">
          <StatCard title="Tốc độ thực tế" value={`${num(rate, 1)} GD/s`} icon={Zap} />
          <StatCard title="p50 / p95 (client)" value={`${num(lat.p50, 0)} / ${num(lat.p95, 0)} ms`} icon={Timer} tone={lat.p95 > 500 ? "HIGH" : "LOW"} />
          <StatCard title="Thời gian" value={`${num(elapsed, 0)} s`} sub={left != null ? `còn ~${num(left, 0)} s` : ""} icon={History} />
          <StatCard title="Nhãn trễ đã gửi" value={num(p.labels_sent || 0)} icon={CheckCircle2} />
        </div>
        <RouteSplit routes={p.routes} />
        {p.errors && Object.keys(p.errors).length > 0 && (
          <div className="flex flex-wrap gap-1.5">{Object.entries(p.errors).map(([k, v]) => <Badge key={k} variant="UNSCORED">{k}: {v}</Badge>)}</div>
        )}
      </CardContent>
    </Card>
  );
}

function Report({ job }) {
  const r = job.result;
  if (!r) return job.error ? <Alert variant="error" icon={XCircle} title="Lỗi">{job.error}</Alert> : <Empty>Không có kết quả.</Empty>;
  const t = r.t_fail_01 || {};
  const c = r.latency_client_ms || {}, s = r.latency_server_ms || {};
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Đã gửi" value={num(r.sent)} sub={`${num(r.duration_s, 1)} s · ${num(r.throughput_tps, 1)} GD/s`} icon={Rocket} />
        <StatCard title="p95 client / server" value={`${num(c.p95, 0)} / ${num(s.p95, 0)} ms`} sub={`p99 ${num(c.p99, 0)} / ${num(s.p99, 0)} ms`} icon={Timer} tone={c.p95 > 500 ? "HIGH" : "LOW"} />
        <StatCard title="T-FAIL-01" value={t.passed ? "ĐẠT" : "KHÔNG ĐẠT"} sub={`${num(t.faults_injected)} payload lỗi đã tiêm`} icon={t.passed ? CheckCircle2 : XCircle} tone={t.passed ? "LOW" : "HIGH"} />
        <StatCard title="Chạy tiếp từ offset" value={num(r.next_offset)} sub={r.stopped_early ? "đã dừng sớm" : "chạy hết"} icon={Gauge} />
      </div>
      <RouteSplit routes={r.routes} />
      {r.faults_by_route && Object.keys(r.faults_by_route).length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader><TableRow><TableHead>Lỗi tiêm</TableHead>{ROUTES.map((k) => <TableHead key={k} className="text-right">{k}</TableHead>)}<TableHead className="text-right">Không phản hồi</TableHead></TableRow></TableHeader>
            <TableBody>{Object.entries(r.faults_by_route).map(([f, v]) => (
              <TableRow key={f}><TableCell className="font-mono text-xs">{f}</TableCell>
                {ROUTES.map((k) => <TableCell key={k} className={cn("text-right tabular", k === "LOW" && v[k] && f !== "duplicate" && "font-bold text-destructive")}>{v[k] || ""}</TableCell>)}
                <TableCell className="text-right tabular">{v.NO_RESPONSE || ""}</TableCell></TableRow>))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

export default function Simulator({ user }) {
  const [st, setSt] = useState(null);
  const [form, setForm] = useState({ dataset: "test", limit: 2000, offset: "", tps: 20, threads: 4, fault_rate: 0.02, label_delay_days: 7, send_labels: true });
  const [sel, setSel] = useState(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const canRun = can(user, "risk_lead", "admin");

  const reload = usePoll(() => api.simulator().then(setSt).catch((e) => toast(e.message, "error")), st?.running ? 1500 : 5000, [Boolean(st?.running)]);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value === "" ? "" : Number(e.target.value) });

  const start = async (preset) => {
    setBusy(true);
    try {
      const p = { ...form, ...(preset || {}) };
      const j = await api.startSimulator({ ...p, offset: p.offset === "" ? null : Number(p.offset) });
      toast(`Đã bắt đầu simulator #${j.id} (offset ${num(j.params.offset)})`);
      reload();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const stop = () => api.stopJob(st.running.id).then(() => { toast("Đã gửi lệnh dừng"); reload(); }).catch((e) => toast(e.message, "error"));

  if (!st) return <><PageHeader title="Simulator" /><Skeleton className="h-64" /></>;
  const noData = !st.data_files[form.dataset];
  const shown = sel ? st.history.find((j) => j.id === sel) : st.history.find((j) => !["RUNNING", "STOPPING"].includes(j.status));
  return (
    <>
      <PageHeader title="Simulator" description="Phát lại giao dịch Sparkov vào API theo thứ tự thời gian, tiêm payload lỗi, gửi nhãn trễ và đo độ trễ." />
      {noData && <Alert variant="warn" icon={FileWarning} title="Thiếu dữ liệu" className="mb-6">Không thấy <code>{form.dataset === "test" ? "fraudTest.csv" : "fraudTrain.csv"}</code> trong <code>data/</code>. Tải Kaggle <code>kartik2112/fraud-detection</code> và đặt vào đó.</Alert>}
      {!canRun && <Alert className="mb-6" title="Chỉ xem">Chỉ Risk Lead hoặc Admin được chạy simulator.</Alert>}
      {st.running && <Live job={st.running} onStop={stop} canRun={canRun} />}

      {canRun && !st.running && (
        <div className="mb-6 grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-2">
            <CardHeaderRow icon={Rocket} title="Chạy nhanh" description={`Tự tiếp tục từ offset ${num(st.next_offset[form.dataset])}`} />
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {PRESETS.map((p, i) => (
                <motion.button key={p.key} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }} disabled={busy || noData} onClick={() => start(p.p)}
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { delay: i * 0.05 } }}
                  className="flex flex-col items-start gap-1 rounded-lg border bg-card p-3 text-left shadow-sm transition-colors hover:border-primary/50 hover:bg-primary/5 disabled:opacity-50">
                  <p.icon className="size-4 text-primary" /><span className="text-sm font-semibold">{p.label}</span><span className="text-xs text-muted-foreground">{p.desc}</span>
                </motion.button>
              ))}
            </CardContent>
          </Card>
          <Card className="lg:col-span-3">
            <CardHeaderRow icon={Settings2} title="Tùy chỉnh" description="Kết quả đúng: không payload lỗi nào thành LOW; UNSCORED = lỗi tiêm trừ duplicate." />
            <CardContent>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Field label="Tập dữ liệu">
                  <NativeSelect value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })}>
                    <option value="test">fraudTest.csv</option><option value="train">fraudTrain.csv</option>
                  </NativeSelect>
                </Field>
                <Field label="Số giao dịch"><Input type="number" min="1" value={form.limit} onChange={set("limit")} /></Field>
                <Field label="Offset (trống = chạy tiếp)"><Input type="number" min="0" value={form.offset} placeholder={String(st.next_offset[form.dataset])} onChange={set("offset")} /></Field>
                <Field label="GD/giây (0 = tối đa)"><Input type="number" min="0" value={form.tps} onChange={set("tps")} /></Field>
                <Field label="Luồng gửi"><Input type="number" min="1" max="32" value={form.threads} onChange={set("threads")} /></Field>
                <Field label="Tỉ lệ payload lỗi"><Input type="number" step="0.01" min="0" max="1" value={form.fault_rate} onChange={set("fault_rate")} /></Field>
                <Field label="Nhãn trễ (ngày)"><Input type="number" min="0" value={form.label_delay_days} onChange={set("label_delay_days")} /></Field>
                <Field label="Gửi nhãn trễ"><div className="flex h-9 items-center"><Switch checked={form.send_labels} onCheckedChange={(v) => setForm({ ...form, send_labels: v })} /></div></Field>
              </div>
              <Button disabled={busy || noData} onClick={() => start()}>{busy ? <Loader2 className="animate-spin" /> : <Play />}Bắt đầu</Button>
            </CardContent>
          </Card>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeaderRow icon={Gauge} title={shown ? `Kết quả lần chạy #${shown.id}` : "Kết quả"} description={shown ? fmtDate(shown.created_at) : null}
            actions={shown && <Badge variant={jobVariant(shown.status)}>{JOB_STATUS[shown.status]}</Badge>} />
          <CardContent>{shown ? <Report job={shown} /> : <Empty icon={Play} title="Chưa chạy lần nào" />}</CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeaderRow icon={History} title="Lịch sử chạy" />
          <Table>
            <TableHeader><TableRow><TableHead>#</TableHead><TableHead>Lúc</TableHead><TableHead className="text-right">Offset</TableHead><TableHead className="text-right">Số GD</TableHead><TableHead>Trạng thái</TableHead></TableRow></TableHeader>
            <TableBody>
              {st.history.map((j) => (
                <TableRow key={j.id} onClick={() => setSel(j.id)} data-state={shown?.id === j.id ? "selected" : undefined} className="cursor-pointer">
                  <TableCell>{j.id}</TableCell><TableCell className="text-xs">{fmtDate(j.created_at)}</TableCell>
                  <TableCell className="text-right tabular">{num(j.params?.offset)}</TableCell><TableCell className="text-right tabular">{num(j.result?.sent ?? j.params?.limit)}</TableCell>
                  <TableCell><Badge variant={jobVariant(j.status)}>{JOB_STATUS[j.status]}</Badge></TableCell>
                </TableRow>
              ))}
              {!st.history.length && <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">Chưa có.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Card>
      </div>
    </>
  );
}
