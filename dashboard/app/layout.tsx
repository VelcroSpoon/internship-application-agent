import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Internship agent",
  description: "Queue, drafts and scores. Nothing here submits anything.",
};

const NAV = [
  { href: "/", label: "Queue" },
  { href: "/applications", label: "Applications" },
  { href: "/evals", label: "Scores" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="flex items-baseline gap-6 border-b border-rule px-6 py-2.5">
          <Link href="/" className="font-semibold tracking-tight">
            internship agent
          </Link>
          <nav className="flex gap-4">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="text-muted hover:text-ink hover:underline underline-offset-4"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <span className="label ml-auto">drafts only · never submits</span>
        </header>
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
