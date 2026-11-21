'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Send,
  RefreshCw,
  RotateCcw,
  Volume2,
  CheckCircle2,
  Globe,
  School,
  Sparkles,
} from 'lucide-react';
import { CampusScope, VoiceNoteMode } from '@/types';

interface VoiceNoteReviewCardProps {
  transcript: string;
  transcriptId: string;
  mode: VoiceNoteMode;
  campusScope: CampusScope;
  recordingSeconds?: number;
  onModeChange: (mode: VoiceNoteMode) => void;
  onCampusScopeChange: (scope: CampusScope) => void;
  onConfirm: (finalTranscript: string) => void;
  onRetranscribe: () => void;
  onCancel: () => void;
  isTranscribing?: boolean;
  isProcessing?: boolean;
}

export const VoiceNoteReviewCard: React.FC<VoiceNoteReviewCardProps> = ({
  transcript,
  mode,
  campusScope,
  recordingSeconds,
  onModeChange,
  onCampusScopeChange,
  onConfirm,
  onRetranscribe,
  onCancel,
  isTranscribing = false,
  isProcessing = false,
}) => {
  const [editedText, setEditedText] = useState(transcript);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync editedText whenever new transcript arrives from re-transcription
  useEffect(() => {
    setEditedText(transcript);
  }, [transcript]);

  // Auto-resize textarea to fit content without scrollbars
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.max(80, textareaRef.current.scrollHeight)}px`;
    }
  }, [editedText]);

  const modeLabels: Record<VoiceNoteMode, string> = {
    auto: 'Auto (FR/TN)',
    derja: 'Derja (تونسي)',
    ar: 'العربية (Fusha)',
    fr: 'Français',
  };

  const campusOptions: CampusScope[] = ['all', 'IIT', 'ISB', 'ISA'];

  return (
    <div className="mx-3 my-2 p-4 bg-gradient-to-b from-blue-50/70 to-white rounded-2xl border-2 border-blue-200 shadow-md transition-all">
      {/* Header bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-blue-100">
        <div className="flex items-center gap-2">
          <div className="p-1.5 bg-blue-600 text-white rounded-lg shadow-xs">
            <Volume2 className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-xs font-bold text-slate-900 flex items-center gap-1.5">
              <span>Vérification de la transcription (OpenAI Whisper)</span>
              <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-emerald-100 text-emerald-800 rounded-md">
                Étape de validation
              </span>
            </h4>
            <p className="text-[11px] text-slate-500">
              {recordingSeconds ? `Enregistrement (${recordingSeconds}s) · ` : ''}
              Vérifiez ou corrigez le texte avant de l'envoyer à VISION.
            </p>
          </div>
        </div>

        {/* Campus Scope Selector Badges */}
        <div className="flex items-center gap-1 bg-white border border-slate-200 p-0.5 rounded-lg text-xs">
          <span className="text-[10px] font-bold text-slate-600 px-1.5 flex items-center gap-0.5">
            <School className="w-3 h-3 text-blue-600" />
            École :
          </span>
          {campusOptions.map((scope) => (
            <button
              key={scope}
              type="button"
              onClick={() => onCampusScopeChange(scope)}
              className={`px-2 py-0.5 rounded text-[11px] font-semibold transition-all ${
                campusScope === scope
                  ? 'bg-blue-600 text-white shadow-2xs'
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {scope === 'all' ? 'Tous' : scope}
            </button>
          ))}
        </div>
      </div>

      {/* Language Mode Selector Bar */}
      <div className="flex items-center gap-2 py-2 text-xs">
        <span className="text-[11px] font-medium text-slate-700 flex items-center gap-1 flex-shrink-0">
          <Globe className="w-3.5 h-3.5 text-blue-600" />
          Mode linguistique Whisper :
        </span>
        <div className="flex flex-wrap gap-1">
          {(['auto', 'derja', 'ar', 'fr'] as VoiceNoteMode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onModeChange(m)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium border transition-colors ${
                mode === m
                  ? 'bg-blue-100 text-blue-800 border-blue-300 font-semibold shadow-2xs'
                  : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              }`}
            >
              {modeLabels[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Editable Textarea (auto-resizing & dir="auto" for natural Arabic/Derja/French alignment) */}
      <div className="relative my-2">
        <textarea
          ref={textareaRef}
          dir="auto"
          value={editedText}
          disabled={isTranscribing || isProcessing}
          onChange={(e) => setEditedText(e.target.value)}
          placeholder="Transcription de votre message vocal (modifiable)..."
          rows={3}
          className="w-full p-3 text-sm text-slate-900 bg-white border border-blue-300 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-600 focus:outline-hidden transition-all shadow-inner leading-relaxed resize-none placeholder:text-slate-500"
        />
        {isTranscribing && (
          <div className="absolute inset-0 bg-white/80 backdrop-blur-xs flex items-center justify-center rounded-xl gap-2 text-xs font-semibold text-blue-700">
            <RefreshCw className="w-4 h-4 animate-spin text-blue-600" />
            <span>Transcription Whisper en cours ({modeLabels[mode]})...</span>
          </div>
        )}
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
        <div className="flex items-center gap-1 text-[11px] text-slate-600">
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
          <span>Vous pouvez modifier le texte ci-dessus avant l'envoi.</span>
        </div>

        <div className="flex items-center gap-2">
          {/* Cancel / Re-record button */}
          <button
            type="button"
            disabled={isTranscribing || isProcessing}
            onClick={onCancel}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:text-red-700 hover:bg-red-50 border border-slate-200 rounded-xl transition-colors disabled:opacity-50"
            title="Supprimer cet enregistrement et recommencer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Annuler / Réenregistrer</span>
          </button>

          {/* Re-transcribe button */}
          <button
            type="button"
            disabled={isTranscribing || isProcessing}
            onClick={onRetranscribe}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-xl transition-colors disabled:opacity-50"
            title="Relancer Whisper sur le même audio avec un autre mode"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isTranscribing ? 'animate-spin' : ''}`} />
            <span>Re-transcrire</span>
          </button>

          {/* Send to SmartCampus button */}
          <button
            type="button"
            disabled={!editedText.trim() || isTranscribing || isProcessing}
            onClick={() => onConfirm(editedText.trim())}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-xl shadow-xs transition-all"
          >
            {isProcessing ? (
              <>
                <Sparkles className="w-3.5 h-3.5 animate-spin" />
                <span>Envoi à VISION...</span>
              </>
            ) : (
              <>
                <Send className="w-3.5 h-3.5" />
                <span>Envoyer à SmartCampus</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
