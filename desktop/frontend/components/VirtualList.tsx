import { ReactNode, useLayoutEffect, useMemo, useRef, useState } from "react";

export interface VRow {
  key: string;
  height: number;
  render: () => ReactNode;
}

/** Windowed list: only the rows in (or near) the viewport are rendered, so very long histories stay fast. */
export default function VirtualList({ rows, overscan = 6, className = "" }: { rows: VRow[]; overscan?: number; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const [viewport, setViewport] = useState(600);

  const offsets = useMemo(() => {
    const o = new Array<number>(rows.length + 1);
    o[0] = 0;
    rows.forEach((r, i) => (o[i + 1] = o[i] + r.height));
    return o;
  }, [rows]);
  const total = offsets[rows.length];

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewport(el.clientHeight));
    ro.observe(el);
    setViewport(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  // first row whose bottom edge is below the viewport top (binary search over cumulative offsets)
  let lo = 0, hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] <= top) lo = mid + 1;
    else hi = mid;
  }
  const start = Math.max(0, lo - overscan);
  let end = lo;
  while (end < rows.length && offsets[end] < top + viewport) end++;
  end = Math.min(rows.length, end + overscan);

  return (
    <div ref={box} className={`overflow-auto ${className}`} onScroll={(e) => setTop(e.currentTarget.scrollTop)}>
      <div style={{ height: total, position: "relative" }}>
        {rows.slice(start, end).map((r, i) => (
          <div key={r.key} style={{ position: "absolute", top: offsets[start + i], height: r.height, left: 0, right: 0 }}>
            {r.render()}
          </div>
        ))}
      </div>
    </div>
  );
}
