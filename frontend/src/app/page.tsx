'use client';

import React, { useState } from 'react';
import { Header } from '@/components/common/Header';
import { PipelineIndicator } from '@/components/common/PipelineIndicator';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { AuthModal } from '@/components/auth/AuthModal';
import { authService } from '@/services/auth-service';
import { CandidateStatus, TrackRecommendation, UserSession } from '@/types';

export default function CandidatePage() {
  const [session, setSession] = useState<UserSession>(() => authService.getSession());
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [status, setStatus] = useState<CandidateStatus>('ANONYMOUS');
  const [selectedTrack, setSelectedTrack] = useState<TrackRecommendation | null>(null);

  const handleAuthSuccess = (newSession: UserSession) => {
    setSession(newSession);
    setStatus('AUTHENTICATED');
  };

  const handleLogout = () => {
    authService.logout();
    setSession(authService.getSession());
    setStatus('ANONYMOUS');
  };

  const handleTrackSelected = (track: TrackRecommendation) => {
    setSelectedTrack(track);
    setStatus('ORIENTED');
    if (session.isAnonymous) {
      setIsAuthOpen(true);
    } else {
      alert(`Filière retenue : ${track.trackName} (${track.school}). Vous pouvez maintenant déposer votre relevé.`);
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
          <div className="max-w-7xl mx-auto flex items-center justify-between w-full">
            <span>
              Filière présélectionnée : <strong>{selectedTrack.trackName}</strong> ({selectedTrack.school})
            </span>
            <button
              onClick={() => setSelectedTrack(null)}
              className="text-[11px] underline opacity-90 hover:opacity-100"
            >
              Modifier
            </button>
          </div>
        </div>
      )}

      <main className="flex-1 p-3 sm:p-6 flex flex-col">
        <ChatContainer
          session={session}
          onOpenAuth={() => setIsAuthOpen(true)}
          onTrackSelected={handleTrackSelected}
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
