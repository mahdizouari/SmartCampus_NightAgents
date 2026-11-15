import React from 'react';
import { TrackRecommendation } from '@/types';
import { Award, Check, Sparkles } from 'lucide-react';

interface TrackCardProps {
  recommendation: TrackRecommendation;
  onSelect?: (track: TrackRecommendation) => void;
}

export const TrackCard: React.FC<TrackCardProps> = ({ recommendation, onSelect }) => {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-xs hover:border-blue-400 hover:shadow-md transition-all flex flex-col justify-between">
      <div>
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="px-2 py-0.5 text-[11px] font-bold rounded-md bg-blue-100 text-blue-700 border border-blue-200">
            {recommendation.school}
          </span>
          <div className="flex items-center gap-1 text-emerald-800 text-xs font-bold bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
            <Sparkles className="w-3 h-3 text-emerald-600" />
            <span>{recommendation.compatibilityScore}% compatibilité</span>
          </div>
        </div>

        <h4 className="font-bold text-slate-900 text-sm mb-1.5 leading-snug">
          {recommendation.trackName}
        </h4>

        <p className="text-xs text-slate-600 mb-3 leading-relaxed">
          {recommendation.justification}
        </p>

        <div className="space-y-1 mb-4">
          <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
            Points forts identifiés :
          </div>
          <div className="flex flex-wrap gap-1">
            {recommendation.matchingStrengths.map((s, idx) => (
              <span
                key={idx}
                className="inline-flex items-center gap-1 text-[11px] bg-slate-100 text-slate-700 px-2 py-0.5 rounded-md"
              >
                <Check className="w-2.5 h-2.5 text-blue-600" />
                {s}
              </span>
            ))}
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => onSelect?.(recommendation)}
        className="w-full mt-2 py-2 px-3 bg-blue-50 hover:bg-blue-600 hover:text-white text-blue-700 border border-blue-200 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors group"
      >
        <Award className="w-3.5 h-3.5 group-hover:scale-110 transition-transform" />
        <span>Retenir cette formation</span>
      </button>
    </div>
  );
};

