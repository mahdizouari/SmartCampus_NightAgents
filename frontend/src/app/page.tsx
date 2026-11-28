'use client';

import React, { useEffect, useState } from 'react';
import { Header } from '@/components/common/Header';
import { PipelineIndicator } from '@/components/common/PipelineIndicator';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { AuthModal } from '@/components/auth/AuthModal';
import { authService } from '@/services/auth-service';
import { chatService } from '@/services/chat-service';
import { CandidateStatus, TrackRecommendation, UserSession } from '@/types';

export default function CandidatePage() {
  const [session, setSession] = useState<UserSession>(() => authService.getSession());
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [status, setStatus] = useState<CandidateStatus>(() => {
    const initial = authService.getSession();
    return initial.status || (initial.isAnonymous ? 'ANONYMOUS' : 'AUTHENTICATED');
  });
  const [selectedTrack, setSelectedTrack] = useState<TrackRecommendation | null>(null);
  const [chatResetKey, setChatResetKey] = useState(0);

  // Initialize / refresh the 7-day signed Visitor guest session asynchronously on mount
  useEffect(() => {
    let cancelled = false;
    authService.ensureGuestSession().then((ensured) => {
      if (!cancelled) {
        setSession(ensured);
        if (ensured.status) {
          setStatus(ensured.status);
        }
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleAuthSuccess = (newSession: UserSession) => {
    setSession(newSession);
    setStatus(newSession.status || 'AUTHENTICATED');
  };

  const handleLogout = () => {
    chatService.resetHistory();
    const visitorSession = authService.logout();
    setSession(visitorSession);
    setStatus('ANONYMOUS');
    setSelectedTrack(null);
    setChatResetKey((prev) => prev + 1);
  };

  const handleTrackSelected = (track: TrackRecommendation) => {
    setSelectedTrack(track);
    if (session.isAnonymous) {
      setStatus('ORIENTED');
      setIsAuthOpen(true);
    } else {
      setStatus('AUTHENTICATED');
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-100/60">
      <Header
        session={session}
        onOpenAuth={() => setIsAuthOpen(true)}
        onLogout={handleLogout}
      />

      <PipelineIndicator currentStatus={status} />

      {selectedTrack && (
        <div className="bg-blue-600 text-white text-xs px-4 py-2 flex items-center justify-between">
          <div className="max-w-7xl mx-auto flex items-center justify-between w-full gap-3">
            <span>
              Filière présélectionnée : <strong>{selectedTrack.trackName}</strong> (
              {selectedTrack.school}) —{' '}
              {session.isAnonymous
                ? 'Connectez-vous en Candidat pour déposer votre relevé officiel.'
                : 'Utilisez le trombone dans le chat pour téléverser votre relevé officiel (VLM).'}
            </span>
            <button
              type="button"
              onClick={() => setSelectedTrack(null)}
              className="text-[11px] underline opacity-90 hover:opacity-100 flex-shrink-0"
            >
              Modifier
            </button>
          </div>
        </div>
      )}

      <main className="flex-1 p-3 sm:p-6 flex flex-col">
        <ChatContainer
          key={chatResetKey}
          session={session}
          onOpenAuth={() => setIsAuthOpen(true)}
          onTrackSelected={handleTrackSelected}
          onStatusChange={setStatus}
        />
      </main>

      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => setIsAuthOpen(false)}
        onSuccess={handleAuthSuccess}
      />
    </div>
  );
}
