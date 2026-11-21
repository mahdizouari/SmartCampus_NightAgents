'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Send,
  Mic,
  Paperclip,
  Sparkles,
  StopCircle,
  RefreshCw,
  X,
  AlertCircle,
  Globe,
  School,
} from 'lucide-react';
import {
  CampusScope,
  ChatMessage,
  TrackRecommendation,
  UserSession,
  VoiceNoteMode,
} from '@/types';
import { chatService } from '@/services/chat-service';
import { voiceNoteService } from '@/services/voice-note-service';
import { ChatMessageItem } from './ChatMessageItem';
import { VoiceNoteReviewCard } from './VoiceNoteReviewCard';

interface ChatContainerProps {
  session: UserSession;
  onOpenAuth: () => void;
  onTrackSelected?: (track: TrackRecommendation) => void;
}

interface ReviewState {
  transcriptId: string;
  transcript: string;
  mode: VoiceNoteMode;
  campusScope: CampusScope;
  recordingSeconds: number;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  session,
  onOpenAuth,
  onTrackSelected,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>(() => chatService.getLocalHistory());
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);

  // Voice note recording state
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [voiceMode, setVoiceMode] = useState<VoiceNoteMode>('auto');
  const [campusScope, setCampusScope] = useState<CampusScope>('all');
  const [recordedAudioBlob, setRecordedAudioBlob] = useState<Blob | null>(null);
  const [micError, setMicError] = useState<string | null>(null);

  // Voice note review state (Step 1 -> Step 2)
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isProcessingVoice, setIsProcessingVoice] = useState(false);

  const endOfChatRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const mediaStreamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    endOfChatRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending, reviewState, isRecording]);

  // Clean up media recorder and stream on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

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

  /**
   * Starts microphone capture with MediaRecorder and error handling.
   */
  const startVoiceRecording = async () => {
    setMicError(null);

    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setMicError("L'enregistrement audio n'est pas supporté sur ce navigateur.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      // Determine best audio mime type supported
      let mimeType = '';
      if (typeof MediaRecorder !== 'undefined') {
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          mimeType = 'audio/webm;codecs=opus';
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          mimeType = 'audio/webm';
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          mimeType = 'audio/mp4';
        } else if (MediaRecorder.isTypeSupported('audio/ogg')) {
          mimeType = 'audio/ogg';
        }
      }

      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        });
        setRecordedAudioBlob(audioBlob);

        // Auto-transcribe using Whisper
        await executeTranscription(audioBlob, voiceMode, campusScope, recordingSeconds);
      };

      recorder.start(250);
      setIsRecording(true);
      setRecordingSeconds(0);

      timerRef.current = setInterval(() => {
        setRecordingSeconds((prev) => prev + 1);
      }, 1000);
    } catch (err: unknown) {
      if (err instanceof Error) {
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
          setMicError("Accès au microphone refusé. Veuillez autoriser l'accès au micro dans votre navigateur.");
        } else if (err.name === 'NotFoundError') {
          setMicError('Aucun microphone détecté sur cet appareil.');
        } else {
          setMicError(`Erreur microphone : ${err.message}`);
        }
      } else {
        setMicError('Impossible de démarrer l’enregistrement audio.');
      }
    }
  };

  /**
   * Stops voice recording and stops audio track.
   */
  const stopVoiceRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    setIsRecording(false);

    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
    }
  };

  /**
   * Cancels active recording without transcribing.
   */
  const cancelVoiceRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    setIsRecording(false);
    setRecordingSeconds(0);
    audioChunksRef.current = [];

    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
    }
  };

  /**
   * Sends audio to backend /api/voice-notes/transcribe (Whisper STT).
   */
  const executeTranscription = async (
    blob: Blob,
    mode: VoiceNoteMode,
    scope: CampusScope,
    seconds: number,
  ) => {
    setIsTranscribing(true);
    setMicError(null);
    try {
      const res = await voiceNoteService.transcribe(blob, mode, scope);
      setReviewState({
        transcriptId: res.transcript_id,
        transcript: res.transcript,
        mode: res.mode,
        campusScope: res.campus_scope,
        recordingSeconds: seconds,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erreur de transcription Whisper.';
      setMicError(msg);
    } finally {
      setIsTranscribing(false);
    }
  };

  /**
   * Re-transcribes the same recorded audio Blob with modified language mode or campus scope.
   */
  const handleRetranscribe = async () => {
    if (!recordedAudioBlob || !reviewState) return;
    await executeTranscription(
      recordedAudioBlob,
      reviewState.mode,
      reviewState.campusScope,
      reviewState.recordingSeconds,
    );
  };

  /**
   * Confirms final transcript and routes it to /api/voice-notes/process (LLM pipeline).
   */
  const handleConfirmAndProcess = async (finalTranscript: string) => {
    if (!reviewState || !finalTranscript.trim()) return;

    setIsProcessingVoice(true);
    try {
      await voiceNoteService.process({
        transcript_id: reviewState.transcriptId,
        final_transcript: finalTranscript,
        campus_scope: reviewState.campusScope,
        mode: reviewState.mode,
      });
      setMessages([...chatService.getLocalHistory()]);
      // Clear review state and audio blob once confirmed and processed
      setReviewState(null);
      setRecordedAudioBlob(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erreur de traitement LLM.';
      setMicError(msg);
    } finally {
      setIsProcessingVoice(false);
    }
  };

  const handleCancelReview = () => {
    setReviewState(null);
    setRecordedAudioBlob(null);
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

  const modeLabels: Record<VoiceNoteMode, string> = {
    auto: 'Auto',
    derja: 'Derja (تونسي)',
    ar: 'العربية',
    fr: 'Français',
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-5xl mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Top Banner */}
      <div className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 flex items-center justify-between text-xs text-slate-700 font-medium">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-semibold text-slate-800">Superviseur VISION en ligne</span>
          <span className="text-slate-400">|</span>
          <span className="text-slate-700">Trilingue (Français · Derja تونسي · Arabizi)</span>
          <span className="text-slate-400">|</span>
          <span className="text-slate-600 font-medium">IIT · ISB · ISA</span>
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

      {/* Microphone Error Notification */}
      {micError && (
        <div className="mx-4 mb-2 p-3 bg-red-50 border border-red-200 rounded-xl flex items-center justify-between text-xs text-red-700">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
            <span>{micError}</span>
          </div>
          <button
            onClick={() => setMicError(null)}
            className="p-1 hover:bg-red-100 rounded text-red-600"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Step 2: Inline Editable Review Card (Whisper STT Output) */}
      {reviewState && (
        <VoiceNoteReviewCard
          transcript={reviewState.transcript}
          transcriptId={reviewState.transcriptId}
          mode={reviewState.mode}
          campusScope={reviewState.campusScope}
          recordingSeconds={reviewState.recordingSeconds}
          onModeChange={(newMode) => setReviewState((prev) => (prev ? { ...prev, mode: newMode } : null))}
          onCampusScopeChange={(newScope) => setReviewState((prev) => (prev ? { ...prev, campusScope: newScope } : null))}
          onConfirm={handleConfirmAndProcess}
          onRetranscribe={handleRetranscribe}
          onCancel={handleCancelReview}
          isTranscribing={isTranscribing}
          isProcessing={isProcessingVoice}
        />
      )}

      {/* Quick Prompts */}
      {!reviewState && (
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
      )}

      {/* Input Bar */}
      <div className="p-3 sm:p-4 bg-white border-t border-slate-200">
        {isRecording ? (
          /* Active Recording State with Wave Animation and Language/Campus Controls */
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-red-50 border-2 border-red-200 rounded-xl px-4 py-3 text-red-700 shadow-sm animate-pulse">
            <div className="flex items-center gap-3">
              <span className="w-3.5 h-3.5 rounded-full bg-red-600 animate-ping" />
              <div>
                <span className="text-xs font-bold text-red-900 block">
                  Enregistrement en cours : {recordingSeconds}s
                </span>
                <span className="text-[11px] text-red-700">
                  Parlez naturellement en Français, Derja tunisienne ou Arabe
                </span>
              </div>
            </div>

            {/* Language & Campus Badges during recording */}
            <div className="flex items-center gap-2 text-xs">
              <span className="px-2 py-0.5 bg-white border border-red-200 text-red-800 rounded-md font-semibold text-[11px]">
                {modeLabels[voiceMode]}
              </span>
              <span className="px-2 py-0.5 bg-white border border-red-200 text-red-800 rounded-md font-semibold text-[11px]">
                {campusScope === 'all' ? 'Tous campus' : campusScope}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={cancelVoiceRecording}
                className="px-2.5 py-1.5 text-xs text-red-700 hover:bg-red-100 rounded-lg font-semibold transition-colors"
              >
                Annuler
              </button>
              <button
                type="button"
                onClick={stopVoiceRecording}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors"
              >
                <StopCircle className="w-4 h-4" />
                <span>Terminer et transcrire (Whisper)</span>
              </button>
            </div>
          </div>
        ) : (
          /* Standard Input Bar with Mic and Language/Campus Selectors */
          <div className="space-y-2">
            {/* Context Filters Row (Campus Scope + Whisper Language Mode) */}
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 pb-1">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-slate-600 flex items-center gap-1">
                  <Globe className="w-3 h-3 text-blue-600" />
                  Mode vocal :
                </span>
                <div className="flex items-center bg-slate-100 p-0.5 rounded-lg">
                  {(['auto', 'derja', 'ar', 'fr'] as VoiceNoteMode[]).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setVoiceMode(m)}
                      className={`px-2 py-0.5 rounded text-[11px] font-medium transition-all ${
                        voiceMode === m
                          ? 'bg-white text-blue-700 shadow-2xs font-semibold'
                          : 'text-slate-600 hover:text-slate-900'
                      }`}
                    >
                      {modeLabels[m]}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-slate-600 flex items-center gap-1">
                  <School className="w-3 h-3 text-blue-600" />
                  Campus visé :
                </span>
                <div className="flex items-center bg-slate-100 p-0.5 rounded-lg">
                  {(['all', 'IIT', 'ISB', 'ISA'] as CampusScope[]).map((scope) => (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => setCampusScope(scope)}
                      className={`px-2 py-0.5 rounded text-[11px] font-medium transition-all ${
                        campusScope === scope
                          ? 'bg-blue-600 text-white shadow-2xs font-semibold'
                          : 'text-slate-600 hover:text-slate-900'
                      }`}
                    >
                      {scope === 'all' ? 'Tous' : scope}
                    </button>
                  ))}
                </div>
              </div>
            </div>

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
                disabled={isTranscribing || isProcessingVoice}
                title="Enregistrer une note vocale (Whisper STT)"
                className="p-2.5 text-slate-600 hover:text-red-600 hover:bg-red-50 rounded-xl transition-colors border border-slate-200 relative group"
              >
                <Mic className="w-4 h-4 text-red-600" />
                <span className="absolute -top-7 left-1/2 -translate-x-1/2 bg-slate-900 text-white text-[10px] py-0.5 px-2 rounded-md whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                  Note vocale ({modeLabels[voiceMode]})
                </span>
              </button>

              <input
                type="text"
                value={input}
                dir="auto"
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
          </div>
        )}
      </div>
    </div>
  );
};
