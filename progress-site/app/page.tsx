"use client";

import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import { useEffect, useMemo, useRef, useState } from "react";
import report from "./feature-data.json";

type Feature = (typeof report.features)[number];
const areas = ["すべて", ...Array.from(new Set(report.features.map((x) => x.area)))];

function tone(value: number) {
  if (value >= 80) return "excellent";
  if (value >= 60) return "good";
  if (value >= 40) return "watch";
  return "risk";
}

export default function Home() {
  const mapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 640 });
  const [area, setArea] = useState("すべて");
  const [selectedName, setSelectedName] = useState(report.features[0].name);
  const visible = useMemo(() => area === "すべて" ? report.features : report.features.filter((x) => x.area === area), [area]);
  const selected = visible.find((x) => x.name === selectedName) ?? visible[0];

  useEffect(() => {
    if (!mapRef.current) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(mapRef.current);
    return () => observer.disconnect();
  }, []);

  const leaves = useMemo(() => {
    const root = hierarchy<{ children?: Feature[] }>({ children: visible }).sum((d) => "statements" in d ? Number(d.statements) : 0);
    return treemap<{ children?: Feature[] }>().size([size.width, size.height]).paddingInner(4).round(true).tile(treemapSquarify)(root).leaves();
  }, [visible, size]);

  const covered = visible.reduce((n, x) => n + x.covered, 0);
  const statements = visible.reduce((n, x) => n + x.statements, 0);
  const coverage = statements ? Math.round(covered / statements * 100) : 0;

  return (
    <main>
      <header className="hero">
        <div><p className="eyebrow">PAINTMASKANIMATOR / VERIFIED PROGRESS</p><h1>機能検証マップ</h1><p className="lead">実装量とテスト結果だけで作る、根拠を追えるTreemapです。</p></div>
        <div className="score"><span>行カバレッジ</span><strong>{coverage}<small>%</small></strong><p>{covered.toLocaleString()} / {statements.toLocaleString()} 実行可能行</p></div>
      </header>

      <section className="method">
        <strong>数値の読み方</strong>
        <span><b>面積</b> = 機能を構成する実行可能行数</span>
        <span><b>色・%</b> = テストで実行された行 ÷ 実行可能行</span>
        <span><b>測定</b> = pytest-cov（{new Date(report.generatedAt).toLocaleDateString("ja-JP")} 更新）</span>
      </section>

      <nav className="filters" aria-label="機能カテゴリ">
        {areas.map((item) => <button key={item} className={area === item ? "active" : ""} onClick={() => setArea(item)}>{item}</button>)}
      </nav>

      <div className="dashboard">
        <section className="map-panel">
          <div className="legend"><span><i className="excellent" />80–100%</span><span><i className="good" />60–79%</span><span><i className="watch" />40–59%</span><span><i className="risk" />0–39%</span></div>
          <div className="treemap" ref={mapRef}>
            {leaves.map((leaf) => {
              const feature = leaf.data as unknown as Feature;
              return <button key={feature.name} className={`tile ${tone(feature.coverage)} ${selected?.name === feature.name ? "selected" : ""}`} style={{ left: leaf.x0, top: leaf.y0, width: leaf.x1 - leaf.x0, height: leaf.y1 - leaf.y0 }} onClick={() => setSelectedName(feature.name)} aria-label={`${feature.name} 行カバレッジ${feature.coverage}%`}>
                <span>{feature.area}</span><strong>{feature.name}</strong><em>{feature.coverage}%</em><small>{feature.statements.toLocaleString()}行</small>
              </button>;
            })}
          </div>
        </section>

        {selected && <aside className="detail">
          <p className="detail-area">{selected.area} / COVERAGE EVIDENCE</p><h2>{selected.name}</h2>
          <div className="primary"><span>テスト実行済み</span><strong>{selected.coverage}%</strong><p>{selected.covered.toLocaleString()} / {selected.statements.toLocaleString()} 行</p></div>
          <dl><div><dt>集計対象モジュール</dt><dd>{selected.modules.map((x) => <code key={x}>{x}</code>)}</dd></div><div><dt>この数値が意味すること</dt><dd>テスト実行時に通ったコード行の割合です。仕様の完成度そのものではなく、現在の実装がどこまで自動検証されているかを表します。</dd></div></dl>
        </aside>}
      </div>
      <footer><span>PaintMaskAnimator</span><span>計測: pytest-cov / 配置: d3-hierarchy</span></footer>
    </main>
  );
}
