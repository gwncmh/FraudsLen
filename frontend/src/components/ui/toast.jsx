import React, { createContext, useCallback, useContext, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function Toaster({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((text, kind = "ok") => {
    const id = Math.random();
    setItems((xs) => [...xs, { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(420px,92vw)] flex-col gap-2">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40 }}
              className={cn("pointer-events-auto flex items-start gap-3 rounded-lg border bg-popover p-3 text-sm shadow-lg",
                t.kind === "error" && "border-destructive/40")}>
              {t.kind === "error" ? <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" /> : <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-low" />}
              <span>{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}
