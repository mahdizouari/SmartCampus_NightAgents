'use client';

import React, { useState } from 'react';
import { Header } from '@/components/common/Header';
import { ResponsableDashboard } from '@/components/responsable/ResponsableDashboard';
import { AuthModal } from '@/components/auth/AuthModal';
import { authService } from '@/services/auth-service';
import { UserSession } from '@/types';

export default function ResponsablePage() {
  const [session, setSession] = useState<UserSession>(() => authService.getSession());
  const [isAuthOpen, setIsAuthOpen] = useState(false);

  const handleAuthSuccess = (newSession: UserSession) => {
    setSession(newSession);
  };

  const handleLogout = () => {
    authService.logout();
    setSession(authService.getSession());
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <Header
        session={session}
        onOpenAuth={() => setIsAuthOpen(true)}
        onLogout={handleLogout}
      />

      <main className="flex-1">
        <ResponsableDashboard />
      </main>

      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => setIsAuthOpen(false)}
        onSuccess={handleAuthSuccess}
      />
    </div>
  );
}

