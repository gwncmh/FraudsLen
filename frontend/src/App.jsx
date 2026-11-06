import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, getToken, setToken } from "@/api";
import { Toaster } from "@/components/ui/toast";
import { AppShell } from "@/components/layout";
import { useHashRoute } from "@/lib/hooks";
import { can } from "@/lib/utils";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Transactions from "@/pages/Transactions";
import Review from "@/pages/Review";
import Simulator from "@/pages/Simulator";
import Models from "@/pages/Models";
import SettingsPage from "@/pages/Settings";

// Điều hướng bằng hash: #/ dashboard · #/transactions · #/review[/<case>] · #/simulator · #/models · #/settings[/<tab>]
export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const route = useHashRoute();

  const loadMe = () => api.me().then(setUser).catch(() => { setToken(""); setUser(null); }).finally(() => setChecking(false));
  useEffect(() => { if (getToken()) loadMe(); }, []);
  useEffect(() => {
    const on = () => { setToken(""); setUser(null); };
    window.addEventListener("fraudlens:unauthorized", on);
    return () => window.removeEventListener("fraudlens:unauthorized", on);
  }, []);

  if (checking) return <div className="grid h-full place-items-center text-muted-foreground"><Loader2 className="size-6 animate-spin" /></div>;
  if (!user) return <Toaster><Login onDone={loadMe} /></Toaster>;

  const logout = async () => { await api.logout().catch(() => {}); setToken(""); setUser(null); window.location.hash = "#/"; };
  // đường dẫn cũ #/cases/<id> và #/queue vẫn dùng được
  let [page, sub] = route.path;
  if (page === "cases" || page === "queue") page = "review";
  if (page === "system" || page === "users" || page === "account") { sub = page; page = "settings"; }
  if (page === "models" && !can(user, "risk_lead", "admin")) page = "dashboard";
  const active = ["transactions", "review", "simulator", "models", "settings"].includes(page) ? page : "dashboard";

  const content = {
    dashboard: <Dashboard user={user} />,
    transactions: <Transactions user={user} query={route.query} />,
    review: <Review user={user} caseId={sub ? Number(sub) : null} />,
    simulator: <Simulator user={user} />,
    models: <Models user={user} />,
    settings: <SettingsPage user={user} tab={sub} onUserChange={loadMe} />,
  }[active];

  return (
    <Toaster>
      <AppShell user={user} active={active} onLogout={logout}>{content}</AppShell>
    </Toaster>
  );
}
