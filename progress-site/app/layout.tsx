import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://sehatakuro.github.io/PaintMaskAnimator/feature-map/"),
  title: "PaintMaskAnimator — 機能検証マップ",
  description: "PaintMaskAnimatorの機能規模と自動テストの行カバレッジを俯瞰するインタラクティブな検証マップ。",
  openGraph: { title: "PaintMaskAnimator — Feature Verification Map", description: "機能の規模を面積、自動テストの行カバレッジを色で表示。", images: ["https://sehatakuro.github.io/PaintMaskAnimator/feature-map/og.png"] },
  twitter: { card: "summary_large_image", images: ["https://sehatakuro.github.io/PaintMaskAnimator/feature-map/og.png"] },
  icons: { icon: "https://sehatakuro.github.io/PaintMaskAnimator/feature-map/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
