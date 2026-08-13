import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Paris Restaurant Map",
  description: "A private map for a curated Paris restaurant collection.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
