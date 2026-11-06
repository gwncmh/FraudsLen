import React, { useState } from "react";
import { motion } from "framer-motion";
import {
  ArrowLeftRight, Boxes, ChevronsLeft, ChevronsRight, ClipboardCheck, LayoutDashboard, LogOut, Monitor, Moon, PlayCircle,
  Search, Settings, ShieldHalf, Sun, UserRound,
} from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LiveDot } from "@/components/fraud";
import { navigate, useLocalState, usePoll, useTheme } from "@/lib/hooks";
import { ROLE_LABELS, can, cn } from "@/lib/utils";

const NAV = [
  { key: "dashboard", href: "#/", label: "Dashboard", icon: LayoutDashboard },
  { key: "transactions", href: "#/transactions", label: "Giao dịch", icon: ArrowLeftRight },
  { key: "review", href: "#/review", label: "Xử lý thủ công", icon: ClipboardCheck, badge: true },
  { key: "simulator", href: "#/simulator", label: "Simulator", icon: PlayCircle },
  { key: "models", href: "#/models", label: "Model", icon: Boxes, roles: ["risk_lead", "admin"] },
  { key: "settings", href: "#/settings", label: "Cài đặt", icon: Settings },
];

function ThemeMenuItems({ theme, setTheme }) {
  return [["light", "Sáng", Sun], ["dark", "Tối", Moon], ["system", "Theo hệ thống", Monitor]].map(([k, l, I]) => (
    <DropdownMenuItem key={k} onSelect={() => setTheme(k)} className={cn(theme === k && "font-semibold text-primary")}><I />{l}</DropdownMenuItem>
  ));
}

export function AppShell({ user, active, onLogout, children }) {
  const [collapsed, setCollapsed] = useLocalState("fraudlens_sidebar_collapsed", false);
  const [theme, setTheme] = useTheme();
  const [openCases, setOpenCases] = useState(null);
  const [healthy, setHealthy] = useState(true);
  const [q, setQ] = useState("");

  usePoll(() => {
    api.stats().then((s) => { setOpenCases(s.open_cases.reduce((a, r) => a + r.n, 0)); setHealthy(true); }).catch(() => setHealthy(false));
  }, 20000);

  const nav = NAV.filter((n) => !n.roles || can(user, ...n.roles));
  const search = (e) => { e.preventDefault(); if (q.trim()) navigate(`/transactions?q=${encodeURIComponent(q.trim())}`); };
  const initial = (user.display_name || user.username).slice(0, 1).toUpperCase();

  return (
    <div className="flex h-full">
      <motion.aside animate={{ width: collapsed ? 68 : 240 }} transition={{ duration: 0.2 }}
        className="sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex">
        <a href="#/" className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4 hover:no-underline">
          <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-primary to-critical text-white shadow">
            <ShieldHalf className="size-4" />
          </div>
          {!collapsed && <div className="leading-tight"><p className="font-bold text-foreground">FraudLens</p><p className="text-[10px] text-muted-foreground">Fraud operations</p></div>}
        </a>
        <nav className="flex flex-1 flex-col gap-1 p-3">
          {nav.map((n) => (
            <a key={n.key} href={n.href} title={n.label}
              className={cn("group relative flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground",
                active === n.key && "bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary")}>
              {active === n.key && <motion.span layoutId="nav-active" className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" />}
              <n.icon className="size-4 shrink-0" />
              {!collapsed && <span className="flex-1">{n.label}</span>}
              {!collapsed && n.badge && openCases > 0 && <Badge variant="HIGH" className="px-1.5">{openCases > 999 ? "999+" : openCases}</Badge>}
            </a>
          ))}
        </nav>
        <div className="border-t border-sidebar-border p-3">
          <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <ChevronsRight /> : <><ChevronsLeft />Thu gọn</>}
          </Button>
        </div>
      </motion.aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur md:px-6">
          {/* điều hướng gọn cho màn hình nhỏ */}
          <div className="flex gap-1 md:hidden">
            {nav.map((n) => <a key={n.key} href={n.href} className={cn("rounded-md p-2", active === n.key && "bg-primary/10 text-primary")}><n.icon className="size-4" /></a>)}
          </div>
          <form onSubmit={search} className="relative hidden max-w-md flex-1 sm:block">
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm mã giao dịch, 4 số cuối thẻ, merchant…" className="pl-8" />
          </form>
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
              <LiveDot ok={healthy} />{healthy ? "Đang theo dõi trực tiếp" : "Mất kết nối API"}
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" title="Giao diện">{theme === "dark" ? <Moon /> : theme === "light" ? <Sun /> : <Monitor />}</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end"><ThemeMenuItems theme={theme} setTheme={setTheme} /></DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 rounded-full p-0.5 pr-2 hover:bg-accent">
                  <span className="grid size-8 place-items-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{initial}</span>
                  <span className="hidden text-left leading-tight sm:block">
                    <span className="block text-sm font-medium">{user.display_name || user.username}</span>
                    <span className="block text-[11px] text-muted-foreground">{ROLE_LABELS[user.role]}</span>
                  </span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{user.display_name || user.username}<p className="text-xs font-normal text-muted-foreground">@{user.username} · {ROLE_LABELS[user.role]}</p></DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => navigate("/settings/account")}><UserRound />Tài khoản</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => navigate("/settings")}><Settings />Cài đặt</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={onLogout} className="text-destructive focus:text-destructive"><LogOut />Đăng xuất</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 p-4 md:p-6">
          <motion.div key={active} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
            {children}
          </motion.div>
        </main>
      </div>
    </div>
  );
}
