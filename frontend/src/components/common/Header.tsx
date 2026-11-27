'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { GraduationCap, Sparkles, UserCheck, ShieldCheck, Compass } from 'lucide-react';
import { UserSession } from '@/types';

interface HeaderProps {
  session?: UserSession;
  onOpenAuth: () => void;
  onLogout: () => void;
}

export const Header: React.FC<HeaderProps> = ({ session, onOpenAuth, onLogout }) => {
  const pathname = usePathname();
  const isAuthenticated = Boolean(session && !session.isAnonymous);

  return (
    <header className="border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Brand */}
        <Link href="/" className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-700 to-indigo-600 flex items-center justify-center text-white shadow-md shadow-blue-500/20">
            <GraduationCap className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-lg text-slate-900 tracking-tight">SmartCampus AI</span>
              <span className="text-[10px] uppercase font-semibold tracking-wider px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 border border-blue-200">
                v3
              </span>
            </div>
            <p className="text-xs text-slate-700 font-medium">IIT · ISB · ISA (Campus NAU Sfax)</p>
          </div>
        </Link>

        {/* Navigation — strictly Visiteur/Candidat space + Responsable portal */}
        <nav className="hidden md:flex items-center gap-1">
          <Link
            href="/"
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              pathname === '/'
                ? 'bg-slate-100 text-blue-700 font-semibold'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-blue-600" />
              Espace Visiteur / Candidat
            </span>
          </Link>
          <Link
            href="/responsable"
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              pathname.startsWith('/responsable')
                ? 'bg-slate-100 text-blue-700 font-semibold'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              Portail Responsable (HITL &amp; BD)
            </span>
          </Link>
        </nav>

        {/* Actor Badge & Auth Actions */}
        <div className="flex items-center gap-2.5">
          {isAuthenticated && session ? (
            <div className="flex items-center gap-2.5">
              <div className="hidden sm:block text-right">
                <div className="text-xs font-semibold text-slate-800">
                  {session.fullName || session.email}
                </div>
                <div className="text-[10px] uppercase tracking-wider font-semibold flex items-center justify-end gap-1">
                  {session.role === 'RESPONSABLE' ? (
                    <span className="text-emerald-700">
                      Responsable {session.school || 'NAU'}
                    </span>
                  ) : (
                    <span className="text-blue-700">Candidat (Vérifié)</span>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={onLogout}
                className="px-3 py-1.5 text-xs font-medium text-slate-600 hover:text-red-600 border border-slate-200 rounded-lg hover:bg-red-50 transition-colors"
              >
                Déconnexion
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="hidden lg:inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-700 border border-slate-200">
                <Compass className="w-3.5 h-3.5 text-blue-600" />
                Visiteur (Sans compte)
              </span>
              <button
                type="button"
                onClick={onOpenAuth}
                className="flex items-center gap-1.5 px-3.5 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs sm:text-sm font-medium shadow-sm transition-all shadow-blue-500/10"
              >
                <UserCheck className="w-4 h-4" />
                <span>Devenir Candidat</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
