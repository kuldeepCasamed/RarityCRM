import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import Providers from "@/components/Providers";

const geist = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Rarity CRM",
  robots: { index: false, follow: false },
  appleWebApp: { capable: true, title: "Rarity CRM", statusBarStyle: "default" },
};

export const viewport: Viewport = { themeColor: "#8c7864", viewportFit: "cover" }; // cover: lets the bottom bar respect the iPhone home indicator

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geist.variable} font-sans antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
