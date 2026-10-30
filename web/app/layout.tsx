import type { Metadata } from "next";
import { Bricolage_Grotesque, Figtree, JetBrains_Mono } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import { LoadingProvider } from "@/components/loading-provider";
import { Toaster } from "@/components/toaster";
import { ConfirmHost } from "@/components/confirm-host";
import "./globals.css";
import "./compact.css";

const display = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-bricolage" });
const body = Figtree({ subsets: ["latin", "latin-ext"], variable: "--font-figtree" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains" });

export const metadata: Metadata = {
  title: "Agriculture is life - Operations Platform",
  description: "Agriculture is life Co., Ltd. fresh produce export and packing operations platform",
  icons: {
    icon: "/logo.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body><LoadingProvider><AppShell>{children}</AppShell></LoadingProvider><ConfirmHost /><Toaster /></body>
    </html>
  );
}
