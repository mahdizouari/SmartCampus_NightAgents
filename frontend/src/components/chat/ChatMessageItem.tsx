import React from 'react';
import { ChatMessage, TrackRecommendation } from '@/types';
import {
  Bot,
  User,
  BookOpen,
  Volume2,
  ShieldAlert,
  ArrowRight,
  FileUp,
  Calendar,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { TrackCard } from './TrackCard';

interface ChatMessageItemProps {
  message: ChatMessage;
  senderLabel?: string;
  onActionTrigger?: (action: string) => void;
  onSelectTrack?: (track: TrackRecommendation) => void;
}

export const ChatMessageItem: React.FC<ChatMessageItemProps> = ({
  message,
  senderLabel = 'Visiteur',
  onActionTrigger,
  onSelectTrack,
}) => {
  const isVision = message.sender === 'vision';
  const summary = message.transcriptSummary;

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
            {isVision ? 'VISION' : senderLabel}
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

          {/* VLM Transcript Extraction Summary Card (VISION 1.4_Reader) */}
          {summary && (
            <div className="mt-3.5 p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-2.5 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-slate-200">
                <div className="font-bold text-slate-900 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>
                    Extraction VLM ({summary.document_type === 'RELEVE_BAC' ? 'Relevé Bac' : 'Relevé Universitaire'})
                  </span>
                </div>
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                    summary.needs_manual_review || summary.discrepancy_detected
                      ? 'bg-amber-50 text-amber-800 border-amber-200'
                      : 'bg-emerald-50 text-emerald-800 border-emerald-200'
                  }`}
                >
                  Confiance globale : {Math.round(summary.overall_confidence * 100)}%
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[11px]">
                <div className="bg-white p-2 rounded-lg border border-slate-200">
                  <span className="text-slate-500 block">Niveau / Étape</span>
                  <span className="font-semibold text-slate-900">
                    {summary.current_education_level || summary.diploma_stage}
                  </span>
                </div>
                <div className="bg-white p-2 rounded-lg border border-slate-200">
                  <span className="text-slate-500 block">Section / Spécialité</span>
                  <span className="font-semibold text-slate-900">
                    {summary.current_specialty || 'Non précisée'}
                  </span>
                </div>
                <div className="bg-white p-2 rounded-lg border border-slate-200">
                  <span className="text-slate-500 block">Moyenne retenue</span>
                  <span className="font-semibold text-blue-700">
                    {summary.declared_overall_avg != null
                      ? `${summary.declared_overall_avg.toFixed(2)} / 20`
                      : 'À confirmer'}
                  </span>
                </div>
              </div>

              {summary.grades.length > 0 && (
                <div className="max-h-44 overflow-y-auto rounded-lg border border-slate-200 bg-white">
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="bg-slate-100 text-slate-600 font-semibold">
                        <th className="py-1.5 px-2.5">Matière</th>
                        <th className="py-1.5 px-2.5">Note</th>
                        <th className="py-1.5 px-2.5">Coef.</th>
                        <th className="py-1.5 px-2.5">Confiance</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {summary.grades.map((g, idx) => (
                        <tr
                          key={g.id || `${g.subject_name}-${idx}`}
                          className={g.needs_confirmation ? 'bg-amber-50/60' : ''}
                        >
                          <td className="py-1.5 px-2.5 font-medium text-slate-800">
                            {g.subject_name}
                          </td>
                          <td className="py-1.5 px-2.5 font-semibold text-slate-900">
                            {g.grade_value}/{g.grade_scale}
                          </td>
                          <td className="py-1.5 px-2.5 text-slate-600">
                            {g.coefficient ?? '—'}
                          </td>
                          <td className="py-1.5 px-2.5">
                            {g.confidence_score != null ? (
                              <span
                                className={`font-semibold ${
                                  g.needs_confirmation ? 'text-amber-700' : 'text-emerald-700'
                                }`}
                              >
                                {Math.round(g.confidence_score * 100)}%
                                {g.needs_confirmation ? ' (à vérifier)' : ''}
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {(summary.needs_manual_review || summary.discrepancy_detected) && (
                <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 flex items-start gap-2 text-[11px]">
                  <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
                  <span>
                    Certains champs présentent une confiance inférieure à 85% ou un écart avec la
                    moyenne déclarée : une vérification humaine (Candidat / Responsable) est activée.
                  </span>
                </div>
              )}
            </div>
          )}

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
                  {cite.snippet && (
                    <p className="text-[11px] text-slate-500 italic mt-0.5">« {cite.snippet} »</p>
                  )}
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
              <span>
                Passage Visiteur → Candidat requis pour téléverser votre relevé officiel et poursuivre la préinscription.
              </span>
            </div>
            <button
              type="button"
              onClick={() => onActionTrigger?.('AUTH_REQUIRED')}
              className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Devenir Candidat</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}

        {message.actionPrompt === 'CONFIRM_SHORTLIST' && (
          <div className="p-3 bg-blue-50 border border-blue-200 rounded-xl flex items-center justify-between gap-3 text-xs">
            <span className="text-blue-900 font-medium">
              Ces parcours vous intéressent ? Enregistrez votre sélection dans votre dossier Candidat.
            </span>
            <button
              type="button"
              onClick={() => onActionTrigger?.('CONFIRM_SHORTLIST')}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Sauvegarder ma shortlist</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}

        {message.actionPrompt === 'UPLOAD_RELEVE' && (
          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 text-emerald-900 font-medium">
              <FileUp className="w-4 h-4 text-emerald-600 flex-shrink-0" />
              <span>
                Téléversez votre relevé de notes (Bac ou Licence — PDF/JPG/PNG) pour pré-remplir votre dossier par VLM.
              </span>
            </div>
            <button
              type="button"
              onClick={() => onActionTrigger?.('UPLOAD_RELEVE')}
              className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Téléverser mon relevé</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}

        {message.actionPrompt === 'BOOK_SLOT' && (
          <div className="p-3 bg-indigo-50 border border-indigo-200 rounded-xl flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 text-indigo-900 font-medium">
              <Calendar className="w-4 h-4 text-indigo-600 flex-shrink-0" />
              <span>
                Dossier prêt : choisissez un créneau d’entretien avec le Responsable de l’école visée.
              </span>
            </div>
            <button
              type="button"
              onClick={() => onActionTrigger?.('BOOK_SLOT')}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg flex items-center gap-1 flex-shrink-0 shadow-xs"
            >
              <span>Demander un créneau</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
