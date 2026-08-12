import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://jokomanato.com/paintmaskanimator/"),
  title: "PaintMaskAnimator — 機能検証マップ",
  description: "PaintMaskAnimatorの機能規模と自動テストの行カバレッジを俯瞰するインタラクティブな検証マップ。",
  openGraph: { title: "PaintMaskAnimator — Feature Verification Map", description: "機能の規模を面積、自動テストの行カバレッジを色で表示。", images: ["https://jokomanato.com/paintmaskanimator/og.png"] },
  twitter: { card: "summary_large_image", images: ["https://jokomanato.com/paintmaskanimator/og.png"] },
  icons: { icon: "https://jokomanato.com/paintmaskanimator/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
