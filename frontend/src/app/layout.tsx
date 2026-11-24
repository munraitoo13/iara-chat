import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";

import "./globals.css";

const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Iara · Atendimento Inteligente",
  description: "Acolhimento clínico, triagem e agendamento humanizado.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR" className={plusJakarta.variable}>
      <body className="min-h-screen antialiased selection:bg-[#0f766e]/15 selection:text-[#0f766e]">
        {children}
      </body>
    </html>
  );
}
