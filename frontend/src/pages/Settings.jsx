import React, { useState } from "react";
import {
  Check, Circle, Database, FileClock, KeyRound, Loader2, Monitor, Moon, Palette, Play, RefreshCw, Search, Server,
  ShieldCheck, Sun, UserPlus, UserRound, Users, Wrench, X,
} from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeaderRow } from "@/components/ui/card";
import { Field, Input, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, DataList, Empty, Json, Progress, Skeleton } from "@/components/ui/misc";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/fraud";
import { navigate, usePoll, useTheme } from "@/lib/hooks";
import { JOB_KINDS, JOB_STATUS, ROLE_LABELS, can, cn, fmtDate, num } from "@/lib/utils";

const SERVICES = [["postgres", "PostgreSQL"], ["redis", "Redis"], ["api", "API"], ["scorer", "Scorer"], ["worker", "LLM worker"], ["monitor", "Monitor"]];
const jobVariant = (s) => (s === "DONE" ? "LOW" : s === "FAILED" ? "HIGH" : s === "STOPPED" ? "secondary" : "info");

// ------------------------------------------------------------------ hệ thống
function jobLine(j) {
  const r = j.result || {};
  if (j.kind === "seed_history") return `nạp ${num(r.rows_seeded)} GD · Redis ${num(r.redis_records)}`;
  if (j.kind === "rebuild_redis") return `Redis ${num(r.redis_records)} bản ghi`;
  if (j.kind === "check_parity") return r.mismatched_rows ? `LỆCH ${num(r.mismatched_rows)}/${num(r.compared)}` : `KHỚP (${num(r.compared)} GD)`;
  if (j.kind === "trace_all") return r.passed ? "T-INT-01 ĐẠT" : "T-INT-01 KHÔNG ĐẠT";
  if (j.kind === "simulator") return `${num(r.sent)} GD · p95 ${num(r.latency_client_ms?.p95, 0)} ms · T-FAIL-01 ${r.t_fail_01?.passed ? "ĐẠT" : "KHÔNG ĐẠT"}`;
  return "xong";
}

function SystemTab({ user }) {
  const [s, setS] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState("");
  const [out, setOut] = useState(null);
  const [tn, setTn] = useState("");
  const [days, setDays] = useState(30);
  const [openJob, setOpenJob] = useState(null);
  const toast = useToast();
  const admin = can(user, "admin");
  const lead = can(user, "admin", "risk_lead");
  const running = jobs.some((j) => j.status === "RUNNING" || j.status === "STOPPING");

  const reload = usePoll(() => {
    api.system().then(setS).catch((e) => toast(e.message, "error"));
    api.jobs().then((d) => setJobs(d.items)).catch(() => {});
  }, running ? 2000 : 10000, [running]);

  const run = async (key, fn, label) => {
    setBusy(key);
    try {
      const r = await fn();
      if (r?.kind) toast(`Đã bắt đầu: ${JOB_KINDS[r.kind] || r.kind} #${r.id}`); else setOut({ label, data: r });
      reload();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  if (!s) return <Skeleton className="h-96" />;
  const done = s.steps.filter((x) => x.done).length;
  const OPS = [
    admin && { key: "seed", title: "Nạp lịch sử", desc: "Nạp N ngày cuối của fraudTrain.csv làm lịch sử thẻ (một lần, trước khi phát lại).", icon: Database,
      extra: <div className="flex items-center gap-2"><Input type="number" min="1" max="90" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-8 w-20" /><span className="text-xs text-muted-foreground">ngày</span></div>,
      fn: () => api.seedHistory(days) },
    admin && { key: "redis", title: "Dựng lại Redis", desc: "Khi Redis mất dữ liệu: dựng lịch sử thẻ từ PostgreSQL.", icon: RefreshCw, fn: api.rebuildRedis },
    lead && { key: "audit", title: "Kiểm tra audit", desc: "Kiểm tra chuỗi băm của nhật ký audit.", icon: ShieldCheck, fn: api.verifyAudit, label: "Kiểm tra audit" },
    lead && { key: "parity", title: "Offline = online", desc: "So feature online với offline trên 20.000 GD gần nhất.", icon: Wrench, fn: () => api.checkParity(20000) },
    lead && { key: "traceall", title: "Truy vết toàn bộ", desc: "T-INT-01: mọi giao dịch/hồ sơ có đủ chuỗi truy vết.", icon: FileClock, fn: api.traceAll },
    lead && { key: "llm", title: "Kiểm tra LLM", desc: "Gọi thử LLM theo cấu hình .env.", icon: Server, fn: api.testLlm, label: "Kiểm tra LLM" },
    lead && { key: "regen", title: "Soạn lại brief", desc: "Xếp mọi hồ sơ MEDIUM/HIGH đang mở vào hàng đợi soạn lại.", icon: RefreshCw, fn: api.regenBriefs, label: "Soạn lại brief" },
    lead && { key: "eval", title: "Chấm brief", desc: "Tỉ lệ nhận định có căn cứ trên 200 brief gần nhất (mục tiêu ≥ 90%).", icon: Check, fn: () => api.evalBriefs(200) },
  ].filter(Boolean);

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeaderRow icon={Check} title="Các bước cài đặt" description={`${done}/${s.steps.length} hoàn thành`}
            actions={<Button size="sm" variant="ghost" onClick={reload}><RefreshCw />Kiểm tra lại</Button>} />
          <CardContent>
            <Progress value={done} max={s.steps.length} className="mb-4" />
            <ol className="space-y-1">
              {s.steps.map((x) => (
                <li key={x.key} className="flex gap-3 rounded-md p-2 hover:bg-muted/40">
                  <span className={cn("mt-0.5 grid size-5 shrink-0 place-items-center rounded-full",
                    x.done ? "bg-low/15 text-low" : x.optional ? "bg-muted text-muted-foreground" : "bg-medium/15 text-medium")}>
                    {x.done ? <Check className="size-3" /> : <Circle className="size-2.5" />}
                  </span>
                  <div className="text-sm">
                    <p className={cn(x.done && "text-muted-foreground")}>{x.title}</p>
                    {!x.done && x.key === "data" && <p className="text-xs text-muted-foreground">Tải Kaggle <code>kartik2112/fraud-detection</code>, đặt <code>fraudTrain.csv</code>, <code>fraudTest.csv</code> vào <code>data\</code></p>}
                    {!x.done && x.key === "model" && <p className="text-xs text-muted-foreground">Chạy <code>start.bat</code> (tự huấn luyện trong Docker)</p>}
                    {!x.done && x.key === "approve" && <Button variant="link" size="sm" className="h-auto p-0" onClick={() => navigate("/models")}>Mở trang Model để duyệt →</Button>}
                    {!x.done && x.key === "seed" && admin && <Button variant="link" size="sm" className="h-auto p-0" onClick={() => run("seed", () => api.seedHistory(days))}>Nạp lịch sử ngay →</Button>}
                    {!x.done && x.key === "replay" && <Button variant="link" size="sm" className="h-auto p-0" onClick={() => navigate("/simulator")}>Mở Simulator →</Button>}
                    {!x.done && x.key === "llm" && <p className="text-xs text-muted-foreground">Sửa <code>LLM_BASE_URL</code>, <code>LLM_API_KEY</code>, <code>LLM_MODEL</code> trong <code>.env</code> rồi chạy lại <code>start.bat</code></p>}
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
        <Card>
          <CardHeaderRow icon={Server} title="Dịch vụ và dữ liệu" />
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-2">
              {SERVICES.map(([k, label]) => {
                const v = s.services[k] || {};
                const detail = k === "api" ? `routing ${v.routing_config || "chưa có"}${v.scorer_breaker_open ? " · NGẮT MẠCH" : ""}`
                  : k === "scorer" ? (v.ok ? v.body?.model_version : v.body?.detail || v.error || `HTTP ${v.status_code}`)
                  : k === "redis" ? (v.ok ? (v.warm ? "đã có lịch sử thẻ" : "chưa warm") : v.error)
                  : v.ok ? "đang chạy" : v.error || (v.status_code ? `HTTP ${v.status_code}` : "không phản hồi");
                return (
                  <div key={k} className="flex items-start gap-2 rounded-lg border p-2.5">
                    <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", v.ok ? "bg-low" : "bg-destructive")} />
                    <div className="min-w-0"><p className="text-sm font-medium">{label}</p><p className="truncate text-xs text-muted-foreground" title={detail}>{detail}</p></div>
                  </div>
                );
              })}
            </div>
            <DataList items={[
              ["GD lịch sử (SEED)", num(s.counts.txn_seed || 0)],
              ["GD đã phát lại", `${num((s.counts.txn_valid || 0) + (s.counts.txn_invalid || 0))} (${num(s.counts.txn_invalid || 0)} lỗi)`],
              ["Hồ sơ / nhãn trễ", `${num(s.counts.cases)} / ${num(s.counts.labels)}`],
              ["Model ACTIVE", <span key="m" className="font-mono text-xs">{s.model.active || "chưa có"} {s.model.active && !s.model.approved && <Badge variant="HIGH">chưa duyệt</Badge>}</span>],
              ["LLM", s.llm.configured ? s.llm.model : "chưa cấu hình (dùng brief mẫu)"],
              ["Monitor chạy lần cuối", fmtDate(s.counts.monitor_last_run)],
              ["Dữ liệu", Object.entries(s.data.files).map(([f, v]) => `${f}: ${v.exists ? `${v.size_mb} MB` : "THIẾU"}`).join(" · ")],
            ]} />
            <p className="text-xs"><a className="text-primary hover:underline" href="http://localhost:3001" target="_blank" rel="noreferrer">Grafana ↗</a> · <a className="text-primary hover:underline" href="http://localhost:9090" target="_blank" rel="noreferrer">Prometheus ↗</a> <span className="text-muted-foreground">(bật bằng start.bat -Monitoring)</span></p>
          </CardContent>
        </Card>
      </div>

      {OPS.length > 0 && (
        <Card>
          <CardHeaderRow icon={Wrench} title="Thao tác vận hành" description="Thay cho các lệnh python -m fraudlens.tools.admin …" />
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {OPS.map((o) => (
                <div key={o.key} className="flex flex-col gap-2 rounded-lg border p-3">
                  <p className="flex items-center gap-2 text-sm font-semibold"><o.icon className="size-4 text-primary" />{o.title}</p>
                  <p className="flex-1 text-xs text-muted-foreground">{o.desc}</p>
                  {o.extra}
                  <Button size="sm" variant="outline" disabled={!!busy} onClick={() => run(o.key, o.fn, o.label)}>
                    {busy === o.key ? <Loader2 className="animate-spin" /> : <Play />}Chạy
                  </Button>
                </div>
              ))}
            </div>
            {out && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between"><p className="text-sm font-semibold">{out.label}</p><Button size="icon" variant="ghost" onClick={() => setOut(null)}><X /></Button></div>
                <Json data={out.data} />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeaderRow icon={Search} title="Truy vết giao dịch" description="Giao dịch → điểm, model → ngưỡng → hồ sơ → brief → thao tác → audit" />
        <CardContent>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (tn.trim()) run("trace", () => api.trace(tn.trim()), `Truy vết ${tn.trim()}`); }}>
            <Input value={tn} onChange={(e) => setTn(e.target.value)} placeholder="Mã giao dịch (trans_num)" />
            <Button type="submit" disabled={busy === "trace"}>{busy === "trace" ? <Loader2 className="animate-spin" /> : <Search />}Truy vết</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeaderRow icon={FileClock} title="Job gần đây" />
        {!jobs.length ? <CardContent><Empty>Chưa có job nào.</Empty></CardContent> : (
          <Table>
            <TableHeader><TableRow><TableHead>#</TableHead><TableHead>Loại</TableHead><TableHead>Lúc / người chạy</TableHead><TableHead>Trạng thái</TableHead><TableHead>Kết quả</TableHead></TableRow></TableHeader>
            <TableBody>{jobs.map((j) => {
              const p = j.progress || {};
              const live = j.status === "RUNNING" || j.status === "STOPPING";
              return (
                <React.Fragment key={j.id}>
                  <TableRow className="cursor-pointer" onClick={() => setOpenJob(openJob === j.id ? null : j.id)}>
                    <TableCell>{j.id}</TableCell><TableCell>{JOB_KINDS[j.kind] || j.kind}</TableCell>
                    <TableCell className="text-xs">{fmtDate(j.created_at)}<br /><span className="text-muted-foreground">{j.created_by}</span></TableCell>
                    <TableCell><Badge variant={jobVariant(j.status)}>{JOB_STATUS[j.status]}</Badge></TableCell>
                    <TableCell className="text-xs">
                      {live ? (p.total ? <Progress value={p.done || 0} max={p.total} className="w-40" /> : p.message)
                        : j.error ? <span className="text-destructive">{j.error}</span> : j.result ? jobLine(j) : "–"}
                    </TableCell>
                  </TableRow>
                  {openJob === j.id && j.result && <TableRow><TableCell colSpan={5}><Json data={j.result} /></TableCell></TableRow>}
                </React.Fragment>
              );
            })}</TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ người dùng
function UsersTab({ user }) {
  const [items, setItems] = useState(null);
  const [f, setF] = useState({ username: "", display_name: "", password: "", role: "analyst" });
  const toast = useToast();
  const reload = usePoll(() => api.users().then((d) => setItems(d.items)).catch((e) => toast(e.message, "error")), 0);
  const update = async (u, data, msg) => { try { await api.updateUser(u.id, data); toast(msg); reload(); } catch (e) { toast(e.message, "error"); } };
  const resetPw = (u) => { const pw = window.prompt(`Mật khẩu mới cho ${u.username} (≥ 8 ký tự, có chữ và số):`); if (pw) update(u, { password: pw }, `Đã đặt lại mật khẩu cho ${u.username}`); };
  const create = async (e) => {
    e.preventDefault();
    try { await api.createUser(f); toast(`Đã tạo ${f.username}`); setF({ username: "", display_name: "", password: "", role: "analyst" }); reload(); }
    catch (ex) { toast(ex.message, "error"); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeaderRow icon={Users} title="Người dùng" description="Fraud Analyst: xử lý hồ sơ · Risk Lead: hồ sơ chuyển cấp, duyệt model, simulator · Admin: cài đặt, người dùng" />
        {!items ? <CardContent><Skeleton className="h-40" /></CardContent> : (
          <Table>
            <TableHeader><TableRow><TableHead>Tài khoản</TableHead><TableHead>Vai trò</TableHead><TableHead>Trạng thái</TableHead><TableHead>Đăng nhập gần nhất</TableHead><TableHead className="text-right">Thao tác</TableHead></TableRow></TableHeader>
            <TableBody>{items.map((u) => (
              <TableRow key={u.id}>
                <TableCell>
                  <div className="flex items-center gap-2.5">
                    <span className="grid size-8 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{u.display_name.slice(0, 1).toUpperCase()}</span>
                    <div><p className="font-medium">{u.display_name}{u.username === user.username && <span className="text-xs text-muted-foreground"> (bạn)</span>}</p><p className="text-xs text-muted-foreground">@{u.username}</p></div>
                  </div>
                </TableCell>
                <TableCell>
                  <NativeSelect className="h-8 w-40" value={u.role} onChange={(e) => update(u, { role: e.target.value }, `${u.username} → ${ROLE_LABELS[e.target.value]}`)}>
                    {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </NativeSelect>
                </TableCell>
                <TableCell>{u.active ? <Badge variant="LOW">Hoạt động</Badge> : <Badge variant="HIGH">Đã khóa</Badge>}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmtDate(u.last_login_at)}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => resetPw(u)}><KeyRound />Đặt lại MK</Button>
                  <Button size="sm" variant="ghost" className={u.active ? "text-destructive" : ""} onClick={() => update(u, { active: !u.active }, u.active ? `Đã khóa ${u.username}` : `Đã mở khóa ${u.username}`)}>
                    {u.active ? "Khóa" : "Mở khóa"}</Button>
                </TableCell>
              </TableRow>))}
            </TableBody>
          </Table>
        )}
      </Card>
      <Card>
        <CardHeaderRow icon={UserPlus} title="Tạo tài khoản" />
        <CardContent>
          <form className="grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-5" onSubmit={create}>
            <Field label="Tên đăng nhập"><Input value={f.username} onChange={set("username")} required /></Field>
            <Field label="Họ tên"><Input value={f.display_name} onChange={set("display_name")} /></Field>
            <Field label="Mật khẩu"><Input type="password" value={f.password} onChange={set("password")} required autoComplete="new-password" /></Field>
            <Field label="Vai trò"><NativeSelect value={f.role} onChange={set("role")}>{Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</NativeSelect></Field>
            <Button type="submit"><UserPlus />Tạo</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ audit trail
function AuditTab() {
  const [entity, setEntity] = useState("");
  const [q, setQ] = useState("");
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(null);
  const [verify, setVerify] = useState(null);
  const reload = usePoll(() => api.audit(Object.fromEntries(Object.entries({ entity, q, limit: 100 }).filter(([, v]) => v !== ""))).then(setD).catch(() => {}), 15000, [entity, q]);
  return (
    <Card>
      <CardHeaderRow icon={FileClock} title="Audit trail" description="Nhật ký chỉ-thêm; mỗi dòng mang băm của dòng trước, sửa trực tiếp bị trigger chặn."
        actions={<>
          {verify && <Badge variant={verify.chain_ok ? "LOW" : "HIGH"}>{verify.chain_ok ? "Chuỗi băm hợp lệ" : `SAI từ seq ${verify.first_bad_seq}`}</Badge>}
          <Button size="sm" variant="outline" onClick={() => api.verifyAudit().then(setVerify)}><ShieldCheck />Kiểm tra chuỗi</Button>
        </>} />
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <NativeSelect className="w-44" value={entity} onChange={(e) => setEntity(e.target.value)}>
            <option value="">Mọi đối tượng</option>{(d?.entities || []).map((x) => <option key={x} value={x}>{x}</option>)}
          </NativeSelect>
          <Input className="max-w-xs" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm: mã, người, hành động…" />
          <Button size="icon" variant="ghost" onClick={reload}><RefreshCw /></Button>
        </div>
        {!d ? <Skeleton className="h-60" /> : !d.items.length ? <Empty>Không có dòng nào.</Empty> : (
          <div className="overflow-hidden rounded-lg border">
            <Table>
              <TableHeader><TableRow><TableHead>Seq</TableHead><TableHead>Thời điểm</TableHead><TableHead>Người / tiến trình</TableHead><TableHead>Hành động</TableHead><TableHead>Đối tượng</TableHead><TableHead>Hash</TableHead></TableRow></TableHeader>
              <TableBody>{d.items.map((a) => (
                <React.Fragment key={a.seq}>
                  <TableRow className="cursor-pointer" onClick={() => setOpen(open === a.seq ? null : a.seq)}>
                    <TableCell className="font-mono text-xs">{a.seq}</TableCell><TableCell className="text-xs">{fmtDate(a.ts)}</TableCell>
                    <TableCell className="text-xs">{a.actor}</TableCell><TableCell><Badge variant="outline" className="font-mono">{a.action}</Badge></TableCell>
                    <TableCell className="text-xs">{a.entity} <span className="font-mono text-muted-foreground">{a.entity_id}</span></TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">{a.hash.slice(0, 12)}…</TableCell>
                  </TableRow>
                  {open === a.seq && <TableRow><TableCell colSpan={6}><Json data={a.payload} /></TableCell></TableRow>}
                </React.Fragment>))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ tài khoản và giao diện
function AccountTab({ user }) {
  const [f, setF] = useState({ old: "", pw: "", confirm: "" });
  const toast = useToast();
  const submit = async (e) => {
    e.preventDefault();
    if (f.pw !== f.confirm) return toast("Mật khẩu nhập lại không khớp", "error");
    try { await api.changePassword(f.old, f.pw); toast("Đã đổi mật khẩu (các phiên khác đã bị đăng xuất)"); setF({ old: "", pw: "", confirm: "" }); }
    catch (ex) { toast(ex.message, "error"); }
  };
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeaderRow icon={UserRound} title="Thông tin tài khoản" />
        <CardContent>
          <DataList items={[["Tên đăng nhập", user.username], ["Họ tên", user.display_name], ["Vai trò", ROLE_LABELS[user.role]],
            ["Kiểu đăng nhập", user.account ? "Tài khoản" : "Token tĩnh (.env)"]]} />
        </CardContent>
      </Card>
      <Card>
        <CardHeaderRow icon={KeyRound} title="Đổi mật khẩu" />
        <CardContent>
          {!user.account ? <p className="text-sm text-muted-foreground">Đăng nhập bằng token tĩnh không đổi được mật khẩu.</p> : (
            <form className="grid gap-3" onSubmit={submit}>
              <Field label="Mật khẩu hiện tại"><Input type="password" value={f.old} onChange={(e) => setF({ ...f, old: e.target.value })} required autoComplete="current-password" /></Field>
              <Field label="Mật khẩu mới" hint="Ít nhất 8 ký tự, có cả chữ và số."><Input type="password" value={f.pw} onChange={(e) => setF({ ...f, pw: e.target.value })} required autoComplete="new-password" /></Field>
              <Field label="Nhập lại mật khẩu mới"><Input type="password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} required autoComplete="new-password" /></Field>
              <div><Button type="submit">Đổi mật khẩu</Button></div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AppearanceTab() {
  const [theme, setTheme] = useTheme();
  return (
    <Card>
      <CardHeaderRow icon={Palette} title="Giao diện" description="Lưu trên trình duyệt này." />
      <CardContent className="grid gap-3 sm:grid-cols-3">
        {[["light", "Sáng", Sun], ["dark", "Tối", Moon], ["system", "Theo hệ thống", Monitor]].map(([k, l, I]) => (
          <button key={k} onClick={() => setTheme(k)} className={cn("flex items-center gap-3 rounded-lg border p-4 text-left transition-colors hover:bg-muted/50",
            theme === k && "border-primary ring-1 ring-primary")}>
            <I className="size-5 text-primary" /><span className="font-medium">{l}</span>{theme === k && <Check className="ml-auto size-4 text-primary" />}
          </button>
        ))}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage({ user, tab }) {
  const tabs = [
    { key: "system", label: "Hệ thống", icon: Server, el: <SystemTab user={user} /> },
    can(user, "admin") && { key: "users", label: "Người dùng", icon: Users, el: <UsersTab user={user} /> },
    can(user, "admin", "risk_lead") && { key: "audit", label: "Audit trail", icon: FileClock, el: <AuditTab /> },
    { key: "account", label: "Tài khoản", icon: UserRound, el: <AccountTab user={user} /> },
    { key: "appearance", label: "Giao diện", icon: Palette, el: <AppearanceTab /> },
  ].filter(Boolean);
  const current = tabs.find((t) => t.key === tab)?.key || "system";
  return (
    <>
      <PageHeader title="Cài đặt" description="Trạng thái hệ thống, thao tác vận hành, người dùng, audit và tài khoản." />
      <Tabs value={current} onValueChange={(v) => navigate(`/settings/${v}`)}>
        <TabsList>{tabs.map((t) => <TabsTrigger key={t.key} value={t.key}><t.icon />{t.label}</TabsTrigger>)}</TabsList>
        {tabs.map((t) => <TabsContent key={t.key} value={t.key}>{t.el}</TabsContent>)}
      </Tabs>
    </>
  );
}
