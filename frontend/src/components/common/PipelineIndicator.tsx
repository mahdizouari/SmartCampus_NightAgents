import React from 'react';
import { CandidateStatus } from '@/types';
import { Compass, FileText, CalendarCheck, CheckCircle2 } from 'lucide-react';

interface PipelineIndicatorProps {
  currentStatus: CandidateStatus;
}

export const PipelineIndicator: React.FC<PipelineIndicatorProps> = ({ currentStatus }) => {
  const getPhaseNumber = (status: CandidateStatus): number => {
    switch (status) {
      case 'ANONYMOUS':
      case 'PROFILING':
      case 'ELIGIBLE':
      case 'INELIGIBLE':
      case 'ORIENTED':
        return 1;
      case 'AUTHENTICATED':
      case 'PREINSCRIBED':
      case 'QCM_DONE':
        return 2;
      case 'SCHEDULED':
      case 'DORMANT':
      case 'ESCALATED':
        return 3;
      case 'INTERVIEWED':
      case 'ADMITTED':
      case 'WAITLIST':
      case 'REJECTED':
      case 'WITHDRAWN':
        return 4;
      default:
        return 1;
    }
  };

  const currentPhase = getPhaseNumber(currentStatus);

  const phases = [
    {
      num: 1,
      title: '1. Visiteur · Découvrir',
      desc: 'Orientation libre sans compte (élève ou parent)',
      icon: Compass,
    },
    {
      num: 2,
      title: '2. Candidat · Préinscription',
      desc: 'Connexion OTP, Relevé VLM & QCM',
      icon: FileText,
    },
    {
      num: 3,
      title: '3. Suivi JARVIS',
      desc: 'Créneau d’entretien & rappels auto',
      icon: CalendarCheck,
    },
    {
      num: 4,
      title: '4. Décision Responsable',
      desc: 'Entretien & validation humaine signée',
      icon: CheckCircle2,
    },
  ];

  return (
    <div className="bg-white border-b border-slate-200 py-3 px-4 sm:px-6">
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-2 overflow-x-auto pb-1 sm:pb-0 scrollbar-none">
        {phases.map((p, idx) => {
          const Icon = p.icon;
          const isActive = currentPhase === p.num;
          const isDone = currentPhase > p.num;

          return (
            <div key={p.num} className="flex items-center gap-2 min-w-max flex-1">
              <div
                className={`flex items-center gap-2.5 px-3 py-1.5 rounded-lg border transition-all ${
                  isActive
                    ? 'border-blue-500 bg-blue-50/70 text-blue-900 shadow-xs'
                    : isDone
                    ? 'border-emerald-200 bg-emerald-50/50 text-emerald-800'
                    : 'border-slate-100 bg-slate-50/50 text-slate-400'
                }`}
              >
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : isDone
                      ? 'bg-emerald-600 text-white'
                      : 'bg-slate-200 text-slate-500'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                </div>
                <div>
                  <div className="text-xs font-bold leading-tight">{p.title}</div>
                  <div className="text-[10px] opacity-80 hidden sm:block">{p.desc}</div>
                </div>
              </div>
              {idx < phases.length - 1 && (
                <div className="h-0.5 w-4 bg-slate-200 flex-shrink-0 hidden lg:block" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
