import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { connection } from "next/server";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DocFlow",
  description: "DocFlow — purchase order extraction and review",
};

// The page may use the whole screen of a phone with a notch (founder,
// 2026-11-05; D-234). Next keeps its defaults for the rest
// (`width=device-width, initial-scale=1`). The body's left and right padding
// below keeps every screen clear of the notch in landscape, and the review
// screen's bottom bar keeps clear of the home indicator itself.
export const viewport: Viewport = {
  viewportFit: "cover",
};

// Every page renders per request (founder, 2026-11-12; D-237): the CSP's
// nonce is new on each request (src/proxy.ts), and a page built ahead of time
// would carry none, so its scripts would be refused. Waiting for the request
// here makes that true of every page, including any added later.
export default async function RootLayout({ children }: LayoutProps<"/">) {
  await connection();
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
        {children}
      </body>
    </html>
  );
}
