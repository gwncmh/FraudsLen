import { useCallback, useEffect, useRef, useState } from "react";

/** Gọi fn ngay và lặp lại mỗi ms (ms = 0: chỉ gọi một lần). Trả về hàm gọi lại thủ công. */
export function usePoll(fn, ms, deps = []) {
  const saved = useRef(fn);
  saved.current = fn;
  const run = useCallback(() => saved.current(), []);
  useEffect(() => {
    run();
    if (!ms) return undefined;
    const t = setInterval(() => { if (!document.hidden) run(); }, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
  return run;
}

/** Điều hướng bằng hash: #/path/sub?x=1 -> { path: ["path","sub"], query } */
export function useHashRoute() {
  const parse = () => {
    const raw = window.location.hash.replace(/^#\/?/, "");
    const [p, qs] = raw.split("?");
    return { path: p ? p.split("/").filter(Boolean) : [], query: Object.fromEntries(new URLSearchParams(qs || "")) };
  };
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export const navigate = (to) => { window.location.hash = to.startsWith("#") ? to : `#${to}`; };

export function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("fraudlens_theme") || "system");
  useEffect(() => {
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.classList.toggle("dark", dark);
    };
    apply();
    localStorage.setItem("fraudlens_theme", theme);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);
  return [theme, setTheme];
}

export function useLocalState(key, initial) {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(key); return s ? JSON.parse(s) : initial; } catch { return initial; }
  });
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* bỏ qua */ } }, [key, v]);
  return [v, setV];
}
