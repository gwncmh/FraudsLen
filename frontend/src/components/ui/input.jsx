import * as React from "react";
import { cn } from "@/lib/utils";

const field =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

const Input = React.forwardRef(({ className, type, ...props }, ref) => (
  <input type={type} className={cn(field, className)} ref={ref} {...props} />
));
Input.displayName = "Input";

const Textarea = React.forwardRef(({ className, ...props }, ref) => (
  <textarea className={cn(field, "min-h-[80px] py-2", className)} ref={ref} {...props} />
));
Textarea.displayName = "Textarea";

/** Select gốc của trình duyệt, style giống shadcn (gọn, không cần thư viện). */
const NativeSelect = React.forwardRef(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(field, "cursor-pointer pr-8 bg-background", className)} {...props}>{children}</select>
));
NativeSelect.displayName = "NativeSelect";

const Label = React.forwardRef(({ className, ...props }, ref) => (
  <label ref={ref} className={cn("text-xs font-medium leading-none text-muted-foreground", className)} {...props} />
));
Label.displayName = "Label";

function Field({ label, hint, children, className }) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      {label && <Label>{label}</Label>}
      {children}
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Switch({ checked, onCheckedChange, disabled }) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onCheckedChange(!checked)}
      className={cn("relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-primary" : "bg-input")}>
      <span className={cn("block size-4 rounded-full bg-background shadow transition-transform", checked ? "translate-x-4" : "translate-x-0.5")} />
    </button>
  );
}

export { Input, Textarea, NativeSelect, Label, Field, Switch };
