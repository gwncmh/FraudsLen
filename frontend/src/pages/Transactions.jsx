import React, { useEffect, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, Download, FilterX, Loader2, RefreshCw, Search } from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DataList, Empty, Separator, Skeleton } from "@/components/ui/misc";
import { CaseStatusBadge, FactorList, PageHeader, RiskGauge, RiskScoreBar, SeverityBadge } from "@/components/fraud";
import { usePoll } from "@/lib/hooks";
import { FEATURE_LABELS, cn, downloadCsv, fmtDate, fmtTs, money, num } from "@/lib/utils";

const PAGE = 50;
const EMPTY = { q: "", route: "", status: "", category: "", min_score: "", sort: "newest" };

function TxnSheet({ id, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setD(null); setErr("");
    if (id) api.transaction(id).then(setD).catch((e) => setErr(e.message));
  }, [id]);
  const t = d?.transaction;
  const f = t?.features || {};
  return (
    <Sheet open={Boolean(id)} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">Giao dịch #{id} {t && <SeverityBadge level={t.route} />}</SheetTitle>
          <SheetDescription className="font-mono text-xs">{t?.trans_num || " "}</SheetDescription>
        </SheetHeader>
        {err && <p className="text-sm text-destructive">{err}</p>}
        {!t ? !err && <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-40" /></div> : (<>
          <div className="flex flex-col items-center gap-2 rounded-xl border bg-muted/30 p-4">
            <RiskGauge score={t.score} tMed={t.t_medium} tHigh={t.t_high} />
            <p className="text-xs text-muted-foreground">Ngưỡng MEDIUM ≥ {num(t.t_medium, 5)} · HIGH ≥ {num(t.t_high, 5)}</p>
          </div>
          {t.case_id && (
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div className="flex items-center gap-2 text-sm">Hồ sơ #{t.case_id} <CaseStatusBadge status={t.case_status} /></div>
              <Button size="sm" asChild><a href={`#/review/${t.case_id}`}>Xử lý<ArrowRight /></a></Button>
            </div>
          )}
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Giao dịch</h3>
            <DataList items={[
              ["Số tiền", <b key="a">{money(t.amt)}</b>], ["Merchant", t.merchant], ["Loại", t.category],
              ["Thẻ", t.card_last4 ? `•••• ${t.card_last4}` : null], ["Bang / dân số", `${t.state || "–"} · ${num(t.city_pop)}`],
              ["Tuổi / khoảng cách", `${num(t.age, 0)} tuổi · ${num(t.distance_km, 1)} km`],
              ["Thời gian GD", fmtTs(t.event_ts)], ["Hệ thống nhận", fmtDate(t.received_at)],
              ["Kiểm tra dữ liệu", t.status === "VALID" ? <Badge key="v" variant="LOW">Hợp lệ</Badge>
                : <span key="v" className="flex flex-wrap gap-1">{(t.validation_errors || []).map((e) => <Badge key={e} variant="UNSCORED">{e}</Badge>)}</span>],
            ]} />
          </section>
          <Separator />
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Chấm điểm</h3>
            <DataList items={[
              ["Trạng thái", `${t.score_status || "–"}${t.score_error ? ` (${t.score_error})` : ""}`],
              ["Điểm thô / hiệu chỉnh", `${num(t.score_raw, 5)} / ${num(t.score, 5)}`],
              ["Model", <span key="m" className="font-mono text-xs">{t.model_version || "–"}</span>],
              ["Routing config", <span key="r" className="font-mono text-xs">{t.config_version || "–"}</span>],
              ["Độ trễ xử lý", t.latency_ms != null ? `${num(t.latency_ms, 1)} ms` : null],
            ]} />
          </section>
          {d.brief?.factors?.length > 0 && (<><Separator /><section className="space-y-2"><h3 className="text-sm font-semibold">Yếu tố ảnh hưởng</h3><FactorList factors={d.brief.factors} /></section></>)}
          {Object.keys(f).length > 0 && (<>
            <Separator />
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Feature (lịch sử thẻ)</h3>
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(f).map(([k, v]) => (
                  <div key={k} className="rounded-md border px-2.5 py-1.5 text-xs">
                    <p className="text-muted-foreground">{FEATURE_LABELS[k] || k}</p><p className="font-semibold tabular">{typeof v === "number" ? num(v, 3) : String(v)}</p>
                  </div>
                ))}
              </div>
            </section>
          </>)}
        </>)}
      </SheetContent>
    </Sheet>
  );
}

export default function Transactions({ query }) {
  const [filters, setFilters] = useState({ ...EMPTY, q: query.q || "" });
  const [qInput, setQInput] = useState(query.q || "");
  const [page, setPage] = useState(0);
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState(query.id ? Number(query.id) : null);

  useEffect(() => { if (query.q !== undefined && query.q !== filters.q) { setFilters({ ...filters, q: query.q }); setQInput(query.q); setPage(0); } }, [query.q]);
  useEffect(() => { if (query.id) setSel(Number(query.id)); }, [query.id]);

  const params = Object.fromEntries(Object.entries({ ...filters, limit: PAGE, offset: page * PAGE }).filter(([, v]) => v !== ""));
  const reload = usePoll(() => {
    setLoading(true);
    api.transactions(params).then(setD).catch(() => {}).finally(() => setLoading(false));
  }, 15000, [JSON.stringify(params)]);

  const set = (k) => (e) => { setPage(0); setFilters({ ...filters, [k]: e.target.value }); };
  const total = d?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const dirty = JSON.stringify(filters) !== JSON.stringify(EMPTY);

  const exportCsv = () => downloadCsv(`fraudlens-giao-dich-trang-${page + 1}.csv`, (d?.items || []).map((t) => ({
    id: t.id, trans_num: t.trans_num, event_time: fmtTs(t.event_ts), amount: t.amt, merchant: t.merchant, category: t.category,
    card_last4: t.card_last4, state: t.state, status: t.status, route: t.route, score: t.score, case_id: t.case_id, case_status: t.case_status,
    validation_errors: (t.validation_errors || []).join("|"),
  })));

  return (
    <>
      <PageHeader title="Toàn bộ giao dịch" description={`${num(total)} giao dịch khớp bộ lọc (không gồm lịch sử SEED)`}>
        <Button variant="outline" size="sm" onClick={exportCsv} disabled={!d?.items?.length}><Download />Xuất CSV</Button>
        <Button variant="outline" size="sm" onClick={reload}>{loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}Làm mới</Button>
      </PageHeader>

      <Card className="mb-4 p-3">
        <div className="grid gap-2 md:grid-cols-[2fr_repeat(5,1fr)_auto]">
          <form className="relative" onSubmit={(e) => { e.preventDefault(); setPage(0); setFilters({ ...filters, q: qInput.trim() }); }}>
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input value={qInput} onChange={(e) => setQInput(e.target.value)} onBlur={() => setFilters((f) => ({ ...f, q: qInput.trim() }))}
              placeholder="Mã GD (tiền tố), 4 số cuối thẻ, merchant…" className="pl-8" />
          </form>
          <NativeSelect value={filters.route} onChange={set("route")}>
            <option value="">Mọi luồng</option>{["HIGH", "MEDIUM", "LOW", "UNSCORED"].map((r) => <option key={r} value={r}>{r}</option>)}
          </NativeSelect>
          <NativeSelect value={filters.status} onChange={set("status")}>
            <option value="">Mọi trạng thái</option><option value="VALID">Dữ liệu hợp lệ</option><option value="INVALID">Dữ liệu lỗi</option>
          </NativeSelect>
          <NativeSelect value={filters.category} onChange={set("category")}>
            <option value="">Mọi loại merchant</option>{(d?.categories || []).map((c) => <option key={c} value={c}>{c}</option>)}
          </NativeSelect>
          <NativeSelect value={filters.min_score} onChange={set("min_score")}>
            <option value="">Mọi điểm</option><option value="0.001">≥ 0,001</option><option value="0.01">≥ 0,01</option>
            <option value="0.1">≥ 0,1</option><option value="0.5">≥ 0,5</option>
          </NativeSelect>
          <NativeSelect value={filters.sort} onChange={set("sort")}>
            <option value="newest">Mới nhận nhất</option><option value="event">Thời gian GD</option><option value="score">Điểm cao nhất</option><option value="amount">Số tiền lớn nhất</option>
          </NativeSelect>
          <Button variant="ghost" size="icon" title="Xóa bộ lọc" disabled={!dirty} onClick={() => { setFilters(EMPTY); setQInput(""); setPage(0); }}><FilterX /></Button>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead><TableHead>Luồng</TableHead><TableHead className="w-40">Điểm rủi ro</TableHead><TableHead className="text-right">Số tiền</TableHead>
              <TableHead>Merchant</TableHead><TableHead>Thẻ</TableHead><TableHead>Thời gian GD</TableHead><TableHead>Dữ liệu</TableHead><TableHead>Hồ sơ</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!d && Array.from({ length: 8 }).map((_, i) => <TableRow key={i}><TableCell colSpan={9}><Skeleton className="h-5" /></TableCell></TableRow>)}
            {d?.items.map((t) => (
              <TableRow key={t.id} onClick={() => setSel(t.id)} data-state={sel === t.id ? "selected" : undefined} className="cursor-pointer">
                <TableCell className="font-mono text-xs text-muted-foreground">{t.id}</TableCell>
                <TableCell>{t.route ? <SeverityBadge level={t.route} /> : "–"}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2"><RiskScoreBar score={t.score} route={t.route} compact /><span className="w-12 text-right text-xs tabular">{t.score != null ? Number(t.score).toFixed(3) : ""}</span></div>
                </TableCell>
                <TableCell className="text-right font-medium tabular">{money(t.amt)}</TableCell>
                <TableCell><p className="max-w-[200px] truncate">{t.merchant?.replace(/^fraud_/, "") || "–"}</p><p className="text-xs text-muted-foreground">{t.category}</p></TableCell>
                <TableCell className="tabular">{t.card_last4 ? `•••• ${t.card_last4}` : "–"}</TableCell>
                <TableCell className="whitespace-nowrap text-xs">{fmtTs(t.event_ts)}</TableCell>
                <TableCell>{t.status === "VALID" ? <Badge variant="LOW">Hợp lệ</Badge> : <Badge variant="UNSCORED" title={(t.validation_errors || []).join(", ")}>{(t.validation_errors || [])[0] || t.status}</Badge>}</TableCell>
                <TableCell>{t.case_id ? <a href={`#/review/${t.case_id}`} onClick={(e) => e.stopPropagation()} className={cn("text-xs font-medium text-primary hover:underline")}>#{t.case_id}</a> : <span className="text-xs text-muted-foreground">—</span>}</TableCell>
              </TableRow>
            ))}
            {d && d.items.length === 0 && <TableRow><TableCell colSpan={9}><Empty icon={Search} title="Không có giao dịch">Thử bỏ bộ lọc hoặc chạy simulator.</Empty></TableCell></TableRow>}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground">
          <span>Trang {page + 1} / {num(pages)} · {num(total)} giao dịch</span>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft />Trước</Button>
            <Button size="sm" variant="outline" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Sau<ChevronRight /></Button>
          </div>
        </div>
      </Card>
      <TxnSheet id={sel} onClose={() => { setSel(null); if (query.id) window.location.hash = "#/transactions"; }} />
    </>
  );
}
