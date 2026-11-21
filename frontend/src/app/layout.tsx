import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'SmartCampus AI — Orientation & Admission IIT · ISB · ISA',
  description:
    'Plateforme agentique multilingue et multimodale d’orientation et d’admission universitaire (Groupe 3 Night Agents, NAU 2026/2027).',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900 font-sans selection:bg-blue-100 selection:text-blue-900">
        {children}
      </body>
    </html>
  );
}
