import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const report = JSON.parse(fs.readFileSync(path.join(root, "progress-site/.coverage-map.json"), "utf8"));

const groups = [
  ["描画キャンバス", "描画", ["canvas.py", "canvas_input_events.py", "canvas_stroke_display.py", "canvas_brush_stabilizer.py"]],
  ["選択・変形", "描画", ["canvas_selection.py", "canvas_transform_mask.py", "geometry.py"]],
  ["画像読み込み", "描画", ["canvas_image_import.py", "main_window_import.py", "imaging.py"]],
  ["オニオンスキン", "アニメーション", ["onion.py", "canvas_onion_interaction.py", "canvas_onion_render.py", "main_window_onion.py"]],
  ["タイムライン", "アニメーション", ["timeline.py", "main_window_timeline_ops.py", "canvas_key_frame.py"]],
  ["再生・タイムリマップ", "アニメーション", ["canvas_playback.py", "main_window_time_remap.py"]],
  ["中割り・Tween", "アニメーション", ["main_window_tween.py"]],
  ["色・パレット", "カラー", ["colors.py", "color_ops.py", "color_panel.py", "main_window_color_interaction.py", "main_window_used_color.py"]],
  ["減色", "カラー", ["color_reduction.py"]],
  ["プロジェクト保存", "ファイル", ["project_io.py", "main_window_project_io.py", "main_window_autosave.py", "document.py", "models.py"]],
  ["書き出し", "ファイル", ["main_window_export.py"]],
  ["ワークスペース・UI", "UI", ["main_window.py", "main_window_docking.py", "main_window_workspace.py", "widgets.py", "toolpanel.py", "actionpanel.py", "history_panel.py", "theme.py"]],
  ["基盤・更新", "基盤", ["__main__.py", "config.py", "logging_setup.py", "updater.py", "pressure.py", "utils.py", "constants.py", "errors.py"]],
];

const files = Object.entries(report.files).reduce((map, [key, value]) => {
  const basename = path.basename(key);
  if (map[basename]) throw new Error(`Duplicate coverage basename: ${basename}`);
  map[basename] = value.summary;
  return map;
}, {});

const assigned = new Set(groups.flatMap(([, , modules]) => modules));
const missing = [...assigned].filter((module) => !files[module]);
if (missing.length) throw new Error(`Coverage data is missing grouped modules: ${missing.join(", ")}`);

const ungrouped = Object.keys(files).filter((module) => !assigned.has(module));
if (ungrouped.length) groups.push(["未分類モジュール", "未分類", ungrouped]);

const features = groups.map(([name, area, modules]) => {
  const summaries = modules.map((module) => files[module]);
  const statements = summaries.reduce((n, x) => n + x.num_statements, 0);
  const covered = summaries.reduce((n, x) => n + x.covered_lines, 0);
  return { name, area, modules, statements, covered, coverage: statements ? Math.round(covered / statements * 100) : 0 };
});

const mappedStatements = features.reduce((total, feature) => total + feature.statements, 0);
if (mappedStatements !== report.totals.num_statements) {
  throw new Error(`Mapped ${mappedStatements} statements, expected ${report.totals.num_statements}`);
}

fs.writeFileSync(path.join(root, "progress-site/app/feature-data.json"), JSON.stringify({
  generatedAt: report.meta.timestamp,
  totalCoverage: Math.round(report.totals.percent_statements_covered),
  features,
}, null, 2) + "\n");
