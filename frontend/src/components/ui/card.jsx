import * as React from "react";
import { cn } from "@/lib/utils";

const Card = React.forwardRef(({ className, ...props }, ref) => (
  // min-w-0: card nằm trong grid/flex được phép co theo cột, không đẩy trang tràn ngang trên màn hình hẹp
  <div ref={ref} className={cn("min-w-0 rounded-xl border bg-card text-card-foreground shadow-sm", className)} {...props} />
));
Card.displayName = "Card";

const CardHeader = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex flex-col space-y-1 p-5 pb-3", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("font-semibold leading-none tracking-tight", className)} {...props} />
));
CardTitle.displayName = "CardTitle";

const CardDescription = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("text-xs text-muted-foreground", className)} {...props} />
));
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-5 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex items-center p-5 pt-0", className)} {...props} />
));
CardFooter.displayName = "CardFooter";

/** Tiêu đề card có vùng thao tác bên phải. */
function CardHeaderRow({ title, description, icon: Icon, actions, className }) {
  return (
    <CardHeader className={cn("flex-row items-start justify-between gap-3 space-y-0", className)}>
      <div className="flex min-w-0 items-start gap-3">
        {Icon && <div className="mt-0.5 shrink-0 rounded-lg bg-primary/10 p-2 text-primary"><Icon className="size-4" /></div>}
        <div className="min-w-0 space-y-1">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </CardHeader>
  );
}

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent, CardHeaderRow };
