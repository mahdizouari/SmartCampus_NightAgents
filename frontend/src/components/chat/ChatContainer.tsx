'use client';

import React, { useState, useRef, useEffect } from 'react';
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
  UserCheck,
  Compass,
} from 'lucide-react';
import {
  CampusScope,
  CandidateStatus,
  ChatMessage,
  TrackRecommendation,
  UserSession,
  VoiceNoteMode,
} from '@/types';
import { chatService } from '@/services/chat-service';
import { documentService } from '@/services/document-service';
import { voiceNoteService } from '@/services/voice-note-service';
import { ChatMessageItem } from './ChatMessageItem';
import { VoiceNoteReviewCard } from './VoiceNoteReviewCard';

interface ChatContainerProps {
  session: UserSession;
  onOpenAuth: () => void;
  onTrackSelected?: (track: TrackRecommendation) => void;
  onStatusChange?: (status: CandidateStatus) => void;
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
  onStatusChange,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>(() => chatService.getLocalHistory());
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [uploadingTranscript, setUploadingTranscript] = useState(false);

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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const recordingSecondsRef = useRef<number>(0);
  const recordingCancelledRef = useRef<boolean>(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const mediaStreamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    endOfChatRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending, uploadingTranscript, reviewState, isRecording]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  const quickPrompts = [
    'Quelles sont les filières d’ingénieur et licences à l’IIT ?',
    'Je suis parent d’élève : quelles formations pour un Bac Mathématiques ?',
    '3aslema! Nheb na3ref kifech nsajel fi l’IIT wala l’ISB',
    'شنوة الشروط باش نتقبل في مرحلة تكوين المهندسين؟',
  ];

  const handleSend = async (customText?: string, isVoiceNote = false) => {
    const textToSend = customText || input;
    if (!textToSend.trim() && !isVoiceNote) return;

    setInput('');
    setSending(true);

    try {
      const botReply = await chatService.sendMessage({
        message: textToSend,
        isVoice: isVoiceNote,
        campus_scope: campusScope,
      });
      setMessages([...chatService.getLocalHistory()]);
      if (botReply.candidateStatus) {
        onStatusChange?.(botReply.candidateStatus);
      }
    } finally {
      setSending(false);
    }
  };

  /**
   * Opens the native transcript file picker for an authenticated Candidate,
   * or prompts Give-or-Keep login if the user is still a Visitor.
   */
  const triggerTranscriptUploadPicker = () => {
    if (session.isAnonymous) {
      onOpenAuth();
      return;
    }
    fileInputRef.current?.click();
  };

  /**
   * Uploads selected transcript file to `/api/v1/documents/extract-and-sync-transcript`
   * and appends VISION 1.4_Reader's structured extraction card in chat.
   */
  const handleTranscriptFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    if (session.isAnonymous) {
      onOpenAuth();
      return;
    }

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const uploadTurn: ChatMessage = {
      id: `usr-doc-${Date.now()}`,
      sender: 'user',
      content: `Dépôt du relevé officiel pour extraction VLM : ${file.name}`,
      timestamp: timeStr,
    };
    chatService.appendMessage(uploadTurn);
    setMessages([...chatService.getLocalHistory()]);
    setUploadingTranscript(true);
    setMicError(null);

    try {
      const syncResult = await documentService.extractAndSyncTranscript(file);
      const readerMsg: ChatMessage = {
        id: `vis-vlm-${Date.now()}`,
        sender: 'vision',
        subAgent: '1.4_Reader',
        actorStage: 'CANDIDATE',
        candidateStatus: 'AUTHENTICATED',
        content: syncResult.needs_manual_review
          ? `J’ai analysé votre relevé (${file.name}) et pré-rempli votre profil Candidat (${syncResult.grades_persisted_count} matières détectées). Certains champs requièrent votre confirmation avant validation finale.`
          : `Votre relevé officiel (${file.name}) a été extrait et synchronisé avec succès dans votre dossier Candidat (${syncResult.grades_persisted_count} matières enregistrées).`,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        transcriptSummary: syncResult,
      };
      chatService.appendMessage(readerMsg);
      setMessages([...chatService.getLocalHistory()]);
      onStatusChange?.('AUTHENTICATED');
    } catch (err: unknown) {
      const errText =
        err instanceof Error
          ? err.message
          : 'Impossible d’extraire le relevé officiel (vérifiez votre connexion Candidat ou le format PDF/PNG/JPG).';
      setMicError(errText);
    } finally {
      setUploadingTranscript(false);
    }
  };

  /**
   * Starts microphone capture with MediaRecorder and error handling.
   */
  const startVoiceRecording = async () => {
    setMicError(null);

    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setMicError('L’enregistrement audio n’est pas supporté sur ce navigateur.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

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
        if (recordingCancelledRef.current) {
          recordingCancelledRef.current = false;
          return;
        }
        const elapsedSeconds = recordingSecondsRef.current;
        const audioBlob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        });
        setRecordedAudioBlob(audioBlob);
        await executeTranscription(audioBlob, voiceMode, campusScope, elapsedSeconds);
      };

      recordingCancelledRef.current = false;
      recordingSecondsRef.current = 0;
      recorder.start(250);
      setIsRecording(true);
      setRecordingSeconds(0);

      timerRef.current = setInterval(() => {
        recordingSecondsRef.current += 1;
        setRecordingSeconds(recordingSecondsRef.current);
      }, 1000);
    } catch (err: unknown) {
      if (err instanceof Error) {
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
          setMicError(
            'Accès au microphone refusé. Veuillez autoriser l’accès au micro dans votre navigateur.',
          );
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

  const cancelVoiceRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    recordingCancelledRef.current = true;
    recordingSecondsRef.current = 0;
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

  const handleRetranscribe = async () => {
    if (!recordedAudioBlob || !reviewState) return;
    await executeTranscription(
      recordedAudioBlob,
      reviewState.mode,
      reviewState.campusScope,
      reviewState.recordingSeconds,
    );
  };

  const handleConfirmAndProcess = async (finalTranscript: string) => {
    if (!reviewState || !finalTranscript.trim()) return;

    setIsProcessingVoice(true);
    try {
      const botMsg = await voiceNoteService.process({
        transcript_id: reviewState.transcriptId,
        final_transcript: finalTranscript,
        campus_scope: reviewState.campusScope,
        mode: reviewState.mode,
      });
      setMessages([...chatService.getLocalHistory()]);
      if (botMsg.candidateStatus) {
        onStatusChange?.(botMsg.candidateStatus);
      }
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
    if (action === 'UPLOAD_RELEVE') {
      triggerTranscriptUploadPicker();
      return;
    }
    if (action === 'AUTH_REQUIRED' || action === 'CONFIRM_SHORTLIST' || action === 'BOOK_SLOT') {
      if (session.isAnonymous) {
        onOpenAuth();
      } else if (action === 'BOOK_SLOT') {
        void handleSend('Je souhaite consulter les créneaux d’entretien disponibles.');
      } else {
        void handleSend('Je confirme ma sélection de filières pour mon dossier Candidat.');
      }
    }
  };

  const modeLabels: Record<VoiceNoteMode, string> = {
    auto: 'Auto',
    derja: 'Derja (تونسي)',
    ar: 'العربية',
    fr: 'Français',
  };

  const senderLabel = session.isAnonymous
    ? 'Visiteur'
    : session.role === 'RESPONSABLE'
    ? `Resp. ${session.school || 'NAU'}`
    : 'Candidat';

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-5xl mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Hidden file picker for VLM Transcript Extraction */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,application/pdf,image/png,image/jpeg,image/webp"
        onChange={handleTranscriptFileSelected}
        className="hidden"
      />

      {/* Top Banner — Dynamic Visitor -> Candidate Lifecycle Indicator */}
      <div className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-700 font-medium">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-semibold text-slate-800">Superviseur VISION en ligne</span>
          <span className="text-slate-400 hidden sm:inline">|</span>
          <span className="text-slate-700 hidden sm:inline">
            Français · Derja تونسي · Arabizi
          </span>
        </div>
        <div className="flex items-center gap-2">
          {session.isAnonymous ? (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200 font-semibold text-[11px]">
              <Compass className="w-3 h-3 text-amber-600" />
              Mode : Visiteur (Sans compte · Élève ou Parent)
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 font-semibold text-[11px]">
              <UserCheck className="w-3 h-3 text-emerald-600" />
              {session.role === 'RESPONSABLE'
                ? `Mode : Responsable (${session.school || 'NAU'})`
                : `Mode : Candidat (Vérifié${session.sessionMerged ? ' · Session fusionnée' : ''})`}
            </span>
          )}
          <button
            type="button"
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
            senderLabel={senderLabel}
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

        {uploadingTranscript && (
          <div className="flex items-center gap-2 text-xs text-emerald-700 font-medium bg-emerald-50 p-3 rounded-xl border border-emerald-200 w-fit">
            <Sparkles className="w-4 h-4 animate-spin text-emerald-600" />
            <span>
              Agent 1.4_Reader (VLM) extrait les matières, coefficients et scores de confiance du relevé...
            </span>
          </div>
        )}

        <div ref={endOfChatRef} />
      </div>

      {/* Error Notification */}
      {micError && (
        <div className="mx-4 mb-2 p-3 bg-red-50 border border-red-200 rounded-xl flex items-center justify-between text-xs text-red-700">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
            <span>{micError}</span>
          </div>
          <button
            type="button"
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
          onModeChange={(newMode) =>
            setReviewState((prev) => (prev ? { ...prev, mode: newMode } : null))
          }
          onCampusScopeChange={(newScope) =>
            setReviewState((prev) => (prev ? { ...prev, campusScope: newScope } : null))
          }
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
          <span className="text-[11px] font-semibold text-slate-700 uppercase flex-shrink-0">
            Suggestions :
          </span>
          {quickPrompts.map((prompt, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => void handleSend(prompt)}
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
          <div className="space-y-2">
            {/* Context Filters Row */}
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
                disabled={uploadingTranscript}
                onClick={triggerTranscriptUploadPicker}
                title={
                  session.isAnonymous
                    ? 'Devenir Candidat pour téléverser votre relevé officiel (VLM)'
                    : 'Téléverser votre relevé de notes officiel (PDF, PNG, JPG)'
                }
                className="p-2.5 text-slate-600 hover:text-emerald-700 hover:bg-emerald-50 rounded-xl transition-colors border border-slate-200 disabled:opacity-40"
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
                    void handleSend();
                  }
                }}
                placeholder={
                  session.isAnonymous
                    ? 'Posez votre question en Visiteur (élève, diplômé ou parent — FR, Derja, Arabizi)...'
                    : 'Posez votre question ou poursuivez votre préinscription Candidat...'
                }
                className="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-sm text-slate-900 focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 focus:bg-white transition-all placeholder:text-slate-500"
              />

              <button
                type="button"
                disabled={!input.trim() || sending}
                onClick={() => void handleSend()}
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
