'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Send, Mic, Paperclip, Sparkles, StopCircle, RefreshCw } from 'lucide-react';
import { ChatMessage, TrackRecommendation, UserSession } from '@/types';
import { chatService } from '@/services/chat-service';
import { ChatMessageItem } from './ChatMessageItem';

interface ChatContainerProps {
  session: UserSession;
  onOpenAuth: () => void;
  onTrackSelected?: (track: TrackRecommendation) => void;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  session,
  onOpenAuth,
  onTrackSelected,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>(() => chatService.getLocalHistory());
  const [input, setInput] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [sending, setSending] = useState(false);

  const endOfChatRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    endOfChatRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  const quickPrompts = [
    'Quelles sont les filières en informatique à l’IIT ?',
    '3aslema! Nheb na3ref kifech nsajel fi l’ISB',
    'شنوة الشروط باش نقبل في مرحلة تكوين المهندسين؟',
    'Quels sont les débouchés en Génie Logiciel ?',
  ];

  const handleSend = async (customText?: string, isVoiceNote = false) => {
    const textToSend = customText || input;
    if (!textToSend.trim() && !isVoiceNote) return;

    setInput('');
    setSending(true);

    try {
      await chatService.sendMessage({
        message: textToSend,
        isVoice: isVoiceNote,
      });
      setMessages([...chatService.getLocalHistory()]);
    } finally {
      setSending(false);
    }
  };

  const startVoiceRecording = () => {
    setIsRecording(true);
    setRecordingSeconds(0);
    timerRef.current = setInterval(() => {
      setRecordingSeconds((prev) => prev + 1);
    }, 1000);
  };

  const stopVoiceRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    setIsRecording(false);
    // Simulate speech-to-text transcript from Whisper
    const simulatedVoiceTexts = [
      '3aslema! 3andi bac math w nheb na3ref les formations fi génie logiciel fi l’IIT.',
      'Bonjour, je souhaite me renseigner sur les modalités d’admission et le coût des études à l’ISB.',
    ];
    const picked = simulatedVoiceTexts[Math.floor(Math.random() * simulatedVoiceTexts.length)];
    handleSend(picked, true);
  };

  const handleActionTrigger = (action: string) => {
    if (action === 'AUTH_REQUIRED' || action === 'CONFIRM_SHORTLIST') {
      if (session.isAnonymous) {
        onOpenAuth();
      } else {
        alert('Votre profil est déjà authentifié. Vos préférences ont été enregistrées.');
      }
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-5xl mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Top Banner */}
      <div className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 flex items-center justify-between text-xs text-slate-700 font-medium">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-semibold text-slate-800">Superviseur VISION en ligne</span>
          <span className="text-slate-400">|</span>
          <span className="text-slate-700">Multilingue (Français · Derja تونسي · Arabizi)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden sm:inline text-slate-600">Mode : Découverte Anonyme</span>
          <button
            onClick={() => {
              setMessages([...chatService.getLocalHistory()]);
            }}
            title="Rafraîchir"
            className="p-1 hover:bg-slate-200 rounded text-slate-500"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Message List */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-5">
        {messages.map((m) => (
          <ChatMessageItem
            key={m.id}
            message={m}
            onActionTrigger={handleActionTrigger}
            onSelectTrack={onTrackSelected}
          />
        ))}

        {sending && (
          <div className="flex items-center gap-2 text-xs text-blue-600 font-medium bg-blue-50/60 p-3 rounded-xl w-fit">
            <Sparkles className="w-4 h-4 animate-spin text-blue-600" />
            <span>VISION analyse votre demande et consulte le catalogue officiel...</span>
          </div>
        )}

        <div ref={endOfChatRef} />
      </div>

      {/* Quick Prompts */}
      <div className="px-4 py-2 bg-slate-50/50 border-t border-slate-100 flex items-center gap-2 overflow-x-auto scrollbar-none">
        <span className="text-[11px] font-semibold text-slate-700 uppercase flex-shrink-0">Suggestions :</span>
        {quickPrompts.map((prompt, idx) => (
          <button
            key={idx}
            onClick={() => handleSend(prompt)}
            className="text-xs bg-white hover:bg-blue-50 hover:text-blue-700 hover:border-blue-300 text-slate-700 px-3 py-1 rounded-full border border-slate-200 transition-colors whitespace-nowrap shadow-2xs"
          >
            {prompt}
          </button>
        ))}
      </div>

      {/* Input Bar */}
      <div className="p-3 sm:p-4 bg-white border-t border-slate-200">
        {isRecording ? (
          <div className="flex items-center justify-between bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-red-700 animate-pulse">
            <div className="flex items-center gap-3">
              <span className="w-3 h-3 rounded-full bg-red-600 animate-ping" />
              <span className="text-xs font-semibold">
                Enregistrement note vocale... {recordingSeconds}s (Parlez en FR, Derja ou Arabizi)
              </span>
            </div>
            <button
              onClick={stopVoiceRecording}
              className="flex items-center gap-1.5 px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors"
            >
              <StopCircle className="w-4 h-4" />
              <span>Terminer et transcrire (Whisper)</span>
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                if (session.isAnonymous) {
                  onOpenAuth();
                } else {
                  alert('Formulaire de téléversement du relevé officiel activé.');
                }
              }}
              title="Téléverser relevé officiel"
              className="p-2.5 text-slate-500 hover:text-blue-600 hover:bg-blue-50 rounded-xl transition-colors border border-slate-200"
            >
              <Paperclip className="w-4 h-4" />
            </button>

            <button
              type="button"
              onClick={startVoiceRecording}
              title="Message vocal (Tunisian STT)"
              className="p-2.5 text-slate-500 hover:text-red-600 hover:bg-red-50 rounded-xl transition-colors border border-slate-200"
            >
              <Mic className="w-4 h-4" />
            </button>

            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Posez votre question (Français, Derja tunisienne, Arabizi)..."
              className="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-sm text-slate-900 focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 focus:bg-white transition-all placeholder:text-slate-500"
            />

            <button
              type="button"
              disabled={!input.trim() || sending}
              onClick={() => handleSend()}
              className="p-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white rounded-xl shadow-xs transition-all flex-shrink-0"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

