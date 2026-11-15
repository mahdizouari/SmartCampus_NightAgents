import React from 'react';
import { ChatMessage, TrackRecommendation } from '@/types';
import { Bot, User, BookOpen, Volume2, ShieldAlert, ArrowRight } from 'lucide-react';
import { TrackCard } from './TrackCard';

interface ChatMessageItemProps {
  message: ChatMessage;
  onActionTrigger?: (action: string) => void;
  onSelectTrack?: (track: TrackRecommendation) => void;
}

export const ChatMessageItem: React.FC<ChatMessageItemProps> = ({
  message,
  onActionTrigger,
  onSelectTrack,
}) => {
  const isVision = message.sender === 'vision';

  return (
    <div className={`flex gap-3 max-w-4xl ${isVision ? '' : 'flex-row-reverse self-end'}`}>
      {/* Avatar */}
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-white ${
          isVision
            ? 'bg-gradient-to-tr from-blue-700 to-indigo-600 shadow-xs'
            : 'bg-slate-700 shadow-xs'
        }`}
      >
        {isVision ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
      </div>

      {/* Bubble Content */}
      <div className={`space-y-2 flex-1 ${isVision ? 'items-start' : 'items-end'}`}>
        {/* Header tags */}
        <div className={`flex items-center gap-2 text-[11px] ${isVision ? '' : 'justify-end'}`}>
          <span className="font-semibold text-slate-700">
            {isVision ? 'VISION' : 'Vous'}
          </span>
          {message.subAgent && (
            <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 font-mono text-[10px] border border-indigo-100">
              Agent {message.subAgent}
            </span>
          )}
          {message.detectedDialect && (
            <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 text-[10px] font-medium border border-amber-200">
              {message.detectedDialect === 'Tunisian_Derja'
                ? 'Derja (تونسي)'
                : message.detectedDialect === 'Arabizi'
                ? 'Arabizi'
                : 'Français'}
            </span>
          )}
          <span className="text-slate-400">{message.timestamp}</span>
        </div>

        {/* Text bubble */}
        <div
          className={`p-4 rounded-2xl text-sm leading-relaxed ${
            isVision
              ? 'bg-white border border-slate-200 text-slate-800 shadow-xs'
              : 'bg-blue-600 text-white shadow-xs ml-auto max-w-lg'
          }`}
        >
          {message.isVoice && (
            <div className="flex items-center gap-2 text-xs font-medium mb-1.5 text-blue-100">
              <Volume2 className="w-3.5 h-3.5 animate-pulse" />
              <span>Note vocale transcrite (Whisper STT)</span>
            </div>
          )}
          <p className="whitespace-pre-wrap">{message.content}</p>

          {/* Citations block (Librarian RAG rule: all facts must be cited) */}
          {message.citations && message.citations.length > 0 && (
            <div className="mt-3 pt-3 border-t border-slate-100 space-y-1">
              <div className="text-[11px] font-semibold text-slate-700 flex items-center gap-1">
                <BookOpen className="w-3 h-3 text-blue-600" />
                <span>Source officielle citée :</span>
              </div>
              {message.citations.map((cite) => (
                <div
                  key={cite.id}
                  className="text-xs bg-slate-50 p-2 rounded-lg border border-slate-200 text-slate-600"
                >
                  <span className="font-medium text-slate-800">{cite.documentTitle}</span>
                  {cite.section && <span className="text-slate-500"> — {cite.section}</span>}
                  {cite.snippet && <p className="text-[11px] text-slate-500 italic mt-0.5">« {cite.snippet} »</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recommendations list */}
        {message.recommendations && message.recommendations.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            {message.recommendations.map((rec) => (
              <TrackCard key={rec.trackCode} recommendation={rec} onSelect={onSelectTrack} />
            ))}
          </div>
        )}

        {/* Action prompts */}
        {message.actionPrompt === 'AUTH_REQUIRED' && (
          <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 text-amber-900 font-medium">
              <ShieldAlert className="w-4 h-4 text-amber-600 flex-shrink-0" />
              <span>Connexion requise pour sauvegarder et poursuivre la pré-inscription.</span>
            </div>
            <button
              onClick={() => onActionTrigger?.('AUTH_REQUIRED')}
              className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Se connecter</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}

        {message.actionPrompt === 'CONFIRM_SHORTLIST' && (
          <div className="p-3 bg-blue-50 border border-blue-200 rounded-xl flex items-center justify-between gap-3 text-xs">
            <span className="text-blue-900 font-medium">
              Ces filières vous intéressent ? Enregistrez votre sélection pour pré-remplir votre dossier.
            </span>
            <button
              onClick={() => onActionTrigger?.('CONFIRM_SHORTLIST')}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Sauvegarder ma shortlist</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

