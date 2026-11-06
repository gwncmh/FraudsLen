import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Activity, BrainCircuit, KeyRound, Loader2, ShieldCheck, ShieldHalf, Workflow } from "lucide-react";
import { api, setToken } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert } from "@/components/ui/misc";

const FEATURES = [
  [Activity, "Chấm điểm thời gian thực", "XGBoost hiệu chỉnh xác suất, p95 ≤ 500 ms, ngắt mạch khi Scorer lỗi."],
  [Workflow, "Phân luồng an toàn khi lỗi", "HIGH → MEDIUM → UNSCORED; payload lỗi không bao giờ thành LOW."],
  [BrainCircuit, "Case Brief do LLM soạn", "Bám playbook, kiểm tra bằng code, có brief mẫu khi LLM lỗi."],
  [ShieldCheck, "Audit có chuỗi băm", "Mọi thao tác truy vết được từ giao dịch tới quyết định."],
];

export default function Login({ onDone }) {
  const [mode, setMode] = useState("login");
  const [cfg, setCfg] = useState(null);
  const [f, setF] = useState({ username: "", display_name: "", password: "", confirm: "", token: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authConfig().then((c) => { setCfg(c); if (c.db_ready && !c.has_users) setMode("register"); }).catch(() => setCfg({ db_ready: false }));
  }, []);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    if (mode === "register" && f.password !== f.confirm) return setErr("Mật khẩu nhập lại không khớp");
    setBusy(true);
    try {
      if (mode === "token") {
        setToken(f.token.trim());
        await api.me();
      } else {
        const r = mode === "login"
          ? await api.login(f.username, f.password)
          : await api.register({ username: f.username, display_name: f.display_name, password: f.password });
        setToken(r.token);
      }
      onDone();
    } catch (ex) {
      setToken("");
      setErr(ex.message === "UNAUTHORIZED" ? "Token không hợp lệ" : ex.message);
    } finally { setBusy(false); }
  };
  const firstUser = cfg?.db_ready && !cfg.has_users;

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-zinc-950 p-12 text-zinc-100 lg:flex lg:flex-col lg:justify-between">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(99,102,241,.35),transparent_45%),radial-gradient(circle_at_80%_80%,rgba(225,29,72,.25),transparent_40%)]" />
        <div className="relative flex items-center gap-2.5 text-lg font-bold">
          <div className="grid size-9 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-rose-600"><ShieldHalf className="size-5" /></div>
          FraudLens
        </div>
        <div className="relative space-y-8">
          <div className="space-y-3">
            <h1 className="text-4xl font-bold leading-tight tracking-tight">Phát hiện gian lận thẻ,<br />từ điểm rủi ro tới quyết định.</h1>
            <p className="max-w-md text-zinc-400">Nền tảng vận hành chống gian lận cho đội Fraud: giám sát, review thủ công và quản trị model trên một giao diện.</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {FEATURES.map(([I, t, d], i) => (
              <motion.div key={t} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.08 }}
                className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur">
                <I className="mb-2 size-5 text-indigo-400" /><p className="font-medium">{t}</p><p className="text-xs text-zinc-400">{d}</p>
              </motion.div>
            ))}
          </div>
        </div>
        <p className="relative text-xs text-zinc-500">Nhóm 10 · Chủ đề 5</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="w-full max-w-sm">
          <Card className="shadow-lg">
            <CardHeader className="space-y-2 pb-4">
              <CardTitle className="text-xl">{mode === "register" ? "Tạo tài khoản" : "Đăng nhập"}</CardTitle>
              <CardDescription>{mode === "register" ? "Tài khoản mới có vai trò Fraud Analyst." : "Chào mừng quay lại FraudLens."}</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={submit} className="grid gap-4">
                {mode !== "token" && (
                  <Tabs value={mode} onValueChange={setMode}>
                    <TabsList className="grid w-full grid-cols-2">
                      <TabsTrigger value="login">Đăng nhập</TabsTrigger>
                      <TabsTrigger value="register" disabled={cfg && !cfg.allow_registration}>Đăng ký</TabsTrigger>
                    </TabsList>
                  </Tabs>
                )}
                {cfg && !cfg.db_ready && <Alert variant="error" title="Chưa kết nối được cơ sở dữ liệu">Kiểm tra container postgres / api.</Alert>}
                {firstUser && mode === "register" && <Alert title="Tài khoản đầu tiên">Tài khoản này sẽ có quyền <b>Admin</b>.</Alert>}
                {mode === "token" ? (
                  <Field label="Token tĩnh (DASHBOARD_TOKENS trong .env)"><Input value={f.token} onChange={set("token")} placeholder="analyst-token" autoFocus /></Field>
                ) : (<>
                  <Field label="Tên đăng nhập"><Input value={f.username} onChange={set("username")} autoComplete="username" placeholder="vd: hung.nguyen" autoFocus required /></Field>
                  {mode === "register" && <Field label="Họ tên hiển thị"><Input value={f.display_name} onChange={set("display_name")} placeholder="Nguyễn Văn A" /></Field>}
                  <Field label="Mật khẩu" hint={mode === "register" ? "Ít nhất 8 ký tự, có cả chữ và số." : null}>
                    <Input type="password" value={f.password} onChange={set("password")} required autoComplete={mode === "login" ? "current-password" : "new-password"} />
                  </Field>
                  {mode === "register" && <Field label="Nhập lại mật khẩu"><Input type="password" value={f.confirm} onChange={set("confirm")} required autoComplete="new-password" /></Field>}
                </>)}
                {err && <p className="text-sm text-destructive">{err}</p>}
                <Button type="submit" disabled={busy} className="w-full">
                  {busy && <Loader2 className="animate-spin" />}{mode === "register" ? "Tạo tài khoản" : "Đăng nhập"}
                </Button>
                <Button type="button" variant="link" size="sm" onClick={() => setMode(mode === "token" ? "login" : "token")}>
                  <KeyRound />{mode === "token" ? "Đăng nhập bằng tài khoản" : "Đăng nhập bằng token tĩnh"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}
