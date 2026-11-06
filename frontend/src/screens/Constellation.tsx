import { KeyboardEvent, PointerEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { money } from "../api";

/** Month as a constellation: x = day, y = cumulative spend, star size = that day's spend.
 *  Single series → no legend (dataviz). Tap/hover/arrow keys select a day; a table view mirrors the data. */
export default function Constellation({ daily, start, end, currency }: {
  daily: { day: string; spent: string }[]; start: string; end: string; currency: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(340);
  const [sel, setSel] = useState<number | null>(null);
  const [draw, setDraw] = useState(true);
  useLayoutEffect(() => {
    const el = wrap.current; if (!el) return;
    // padding-box width minus the panel's horizontal padding = drawable width
    const measure = () => {
      const cs = getComputedStyle(el);
      const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      if (inner > 0) setW(Math.max(260, Math.round(inner)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    addEventListener("resize", measure);
    return () => { ro.disconnect(); removeEventListener("resize", measure); };
  }, []);
  useEffect(() => {
    const t = setTimeout(() => setDraw(false), 2000); // one orchestrated moment, then static
    return () => clearTimeout(t);
  }, []);

  const H = 150, padX = 14, padTop = 18, padBot = 22;
  const model = useMemo(() => {
    const s = new Date(start + "T00:00:00"), e = new Date(end + "T00:00:00");
    const n = Math.round((+e - +s) / 864e5);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const lastPast = Math.min(n - 1, Math.floor((+today - +s) / 864e5));
    const by = new Map(daily.map((d) => [d.day, Number(d.spent)]));
    let cum = 0;
    const days = Array.from({ length: n }, (_, i) => {
      const d = new Date(s); d.setDate(s.getDate() + i);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const spent = by.get(iso) ?? 0;
      if (i <= lastPast) cum += spent;
      return { i, iso, date: d, spent, cum, past: i <= lastPast };
    });
    const maxCum = Math.max(cum, 1), maxDay = Math.max(...days.map((d) => d.spent), 1);
    return { n, days, maxCum, maxDay, lastPast, total: cum };
  }, [daily, start, end]);

  const x = (i: number) => padX + (i * (w - padX * 2)) / Math.max(model.n - 1, 1);
  const y = (v: number) => padTop + (1 - v / model.maxCum) * (H - padTop - padBot);
  const r = (v: number) => (v > 0 ? 4 + 7 * Math.sqrt(v / model.maxDay) : 2); // ≥8px diameter for real marks
  const past = model.days.filter((d) => d.past);
  const pts = past.map((d) => `${x(d.i).toFixed(1)},${y(d.cum).toFixed(1)}`).join(" ");
  const len = past.reduce((a, d, k) => k ? a + Math.hypot(x(d.i) - x(past[k - 1].i), y(d.cum) - y(past[k - 1].cum)) : 0, 0);

  const pick = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - box.left) - padX) / ((w - padX * 2) / Math.max(model.n - 1, 1)));
    setSel(Math.max(0, Math.min(model.lastPast, i)));
  };
  const key = (e: KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    setSel((s) => Math.max(0, Math.min(model.lastPast, (s ?? model.lastPast) + (e.key === "ArrowRight" ? 1 : -1))));
  };
  const cur = sel !== null ? model.days[sel] : null;
  const fmtDay = (d: Date) => d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

  return (
    <div className={"sky" + (draw ? " draw" : "")} ref={wrap}>
      <svg width={w} height={H} viewBox={`0 0 ${w} ${H}`} role="img" tabIndex={0}
        aria-label={`Spending so far this month: ${money(model.total, currency)} over ${past.length} days. Use left and right arrow keys to read each day.`}
        onPointerMove={(e) => e.pointerType === "mouse" && pick(e)} onPointerDown={pick}
        onPointerLeave={(e) => e.pointerType === "mouse" && setSel(null)} onKeyDown={key}
        onPointerUp={(e) => e.currentTarget.blur() /* focus ring is for keyboard users */}>
        <line className="sky-grid" x1={padX} x2={w - padX} y1={y(model.maxCum)} y2={y(model.maxCum)} />
        {model.days.filter((d) => !d.past).map((d) => <circle key={d.iso} className="sky-future" cx={x(d.i)} cy={H - padBot} r={1.6} />)}
        {cur && <line className="sky-cursor" x1={x(cur.i)} x2={x(cur.i)} y1={padTop - 6} y2={H - padBot} />}
        <polyline className="sky-line" points={pts} style={{ ["--len" as string]: len.toFixed(0) }} />
        {past.map((d) => d.spent > 0
          ? <circle key={d.iso} className={"sky-star" + (sel === d.i ? " sel" : "")} cx={x(d.i)} cy={y(d.cum)} r={r(d.spent) + (sel === d.i ? 2 : 0)}
              style={{ ["--d" as string]: `${Math.round((d.i / Math.max(model.lastPast, 1)) * 1000)}ms` }} />
          : <circle key={d.iso} className="sky-zero" cx={x(d.i)} cy={y(d.cum)} r={2} />)}
        <text className="sky-axis" x={padX} y={H - 4}>{model.days[0]?.date.getDate()}</text>
        <text className="sky-axis" x={w - padX} y={H - 4} textAnchor="end">{model.n}</text>
      </svg>
      <div className="readout" aria-live="polite">
        {cur
          ? <><span>{fmtDay(cur.date)}</span><span><strong>{money(cur.spent, currency)}</strong><span className="muted">, {money(cur.cum, currency)} so far</span></span></>
          : <span className="muted">Each star is a day. Bigger stars, bigger spending.</span>}
      </div>
      <details className="table-view">
        <summary>Show as a table</summary>
        <table>
          <thead><tr><th className="sr-only">Day</th><th className="sr-only">Spent</th></tr></thead>
          <tbody>
            {past.filter((d) => d.spent > 0).map((d) => (
              <tr key={d.iso}><td>{fmtDay(d.date)}</td><td>{money(d.spent, currency)}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
