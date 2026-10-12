import type { Metadata } from "next";
import "./globals.css";
import { ApiProvider } from "@/components/statline/api-context";
import { SiteHeader, SiteFooter } from "@/components/statline/site-shell";
export const metadata: Metadata = {
  metadataBase: new URL("https://statline.dev"),
  title: { default: "StatLine — Make raw data easier to understand", template: "%s · StatLine" },
  description: "Open-source scoring infrastructure. Turn raw data into structured, explainable scores with adapters, portable StatPacks, and one engine for Python, local tools, and the API.",
  openGraph: { title: "StatLine — Make raw data easier to understand", description: "One scorer. Run it locally, build it into your application, or call it over the API.", type: "website" },
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><head><script src="/statline-config.js" /></head><body className="antialiased"><ApiProvider><a href="#main-content" className="skip">Skip to content</a><SiteHeader/>{children}<SiteFooter/></ApiProvider></body></html>;
}
