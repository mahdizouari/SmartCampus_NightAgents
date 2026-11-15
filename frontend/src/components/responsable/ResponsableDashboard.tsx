'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { CandidateDossier, HumanDecision, SchoolCode } from '@/types';
import { responsableService } from '@/services/responsable-service';
import {
  ShieldCheck,
  Search,
  CheckCircle,
  Clock,
  XCircle,
  Eye,
  CheckCircle2,
  Calendar,
  AlertTriangle,
  X,
} from 'lucide-react';

export const ResponsableDashboard: React.FC = () => {
  const [dossiers, setDossiers] = useState<CandidateDossier[]>([]);
  const [selectedSchool, setSelectedSchool] = useState<SchoolCode | 'ALL'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);

  // Selected dossier for Brief Modal
  const [activeDossier, setActiveDossier] = useState<CandidateDossier | null>(null);

  // Decision Modal State
  const [decisionDossier, setDecisionDossier] = useState<CandidateDossier | null>(null);
  const [decisionChoice, setDecisionChoice] = useState<HumanDecision>('ADMITTED');
  const [decisionNotes, setDecisionNotes] = useState('');
  const [submittingDecision, setSubmittingDecision] = useState(false);

  const fetchDossiers = useCallback(async (school: SchoolCode | 'ALL') => {
    setLoading(true);
    try {
      const data = await responsableService.getDossiers(school === 'ALL' ? undefined : school);
      setDossiers(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    responsableService
      .getDossiers(selectedSchool === 'ALL' ? undefined : selectedSchool)
      .then((data) => {
        if (!ignore) {
          setDossiers(data);
        }
      });
    return () => {
      ignore = true;
    };
  }, [selectedSchool]);

  const handleDecisionSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!decisionDossier) return;

    setSubmittingDecision(true);
    try {
      await responsableService.submitHumanDecision({
        dossierId: decisionDossier.id,
        decision: decisionChoice,
        notes: decisionNotes,
        responsableName: 'Dr. Responsable NAU',
      });
      setDecisionDossier(null);
      setDecisionNotes('');
      await fetchDossiers(selectedSchool);
    } finally {
      setSubmittingDecision(false);
    }
  };

  const filteredDossiers = dossiers.filter((d) => {
    const matchesSearch =
      d.fullName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.candidateNumber.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.targetTrack.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesSearch;
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'ADMITTED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <CheckCircle className="w-3 h-3" /> Admis (Validé)
          </span>
        );
      case 'WAITLIST':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
            <Clock className="w-3 h-3" /> Liste d’attente
          </span>
        );
      case 'REJECTED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-red-100 text-red-800 border border-red-200">
            <XCircle className="w-3 h-3" /> Non retenu
          </span>
        );
      case 'INTERVIEWED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
            <Calendar className="w-3 h-3" /> Entretien passé
          </span>
        );
      case 'SCHEDULED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200">
            <Clock className="w-3 h-3" /> Entretien planifié
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      {/* Top Section */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900">Portail NAU Responsable</h1>
            <span className="px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 text-xs font-bold border border-emerald-200 flex items-center gap-1">
              <ShieldCheck className="w-3.5 h-3.5" />
              Human-in-the-Loop
            </span>
          </div>
          <p className="text-sm text-slate-700 mt-1 font-medium">
            Supervision des candidatures IIT, ISB et ISA, consultation des fiches d’entretien synthétiques et validation finale.
          </p>
        </div>

        {/* HITL Rule reminder box */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-start gap-2.5 max-w-md">
          <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-amber-900 leading-snug font-medium">
            <strong>Règle d’Or SmartCampus :</strong> Aucun agent n’admet ou ne refuse un candidat. Seul le Responsable pédagogique prend et signe la décision officielle.
          </p>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-2xs flex flex-col sm:flex-row items-center justify-between gap-4">
        {/* School Tabs */}
        <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-lg w-full sm:w-auto">
          {(['ALL', 'IIT', 'ISB', 'ISA'] as const).map((school) => (
            <button
              key={school}
              onClick={() => setSelectedSchool(school)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all ${
                selectedSchool === school
                  ? 'bg-white text-blue-700 shadow-2xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {school === 'ALL' ? 'Toutes les écoles' : school}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
          <input
            type="text"
            placeholder="Rechercher nom, n° dossier, filière..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          />
        </div>
      </div>

      {/* Dossiers Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                <th className="py-3 px-4">Candidat</th>
                <th className="py-3 px-4">École & Filière</th>
                <th className="py-3 px-4">Score Orientation</th>
                <th className="py-3 px-4">Relevé (OCR)</th>
                <th className="py-3 px-4">QCM</th>
                <th className="py-3 px-4">Statut</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-sm">
              {loading ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-400 text-xs">
                    Chargement des dossiers...
                  </td>
                </tr>
              ) : filteredDossiers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-400 text-xs">
                    Aucun dossier ne correspond aux critères.
                  </td>
                </tr>
              ) : (
                filteredDossiers.map((d) => (
                  <tr key={d.id} className="hover:bg-slate-50/70 transition-colors">
                    <td className="py-3.5 px-4">
                      <div className="font-semibold text-slate-900 text-xs">{d.fullName}</div>
                      <div className="text-[11px] text-slate-500 font-mono">{d.candidateNumber}</div>
                    </td>
                    <td className="py-3.5 px-4">
                      <div className="flex items-center gap-1.5">
                        <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-blue-100 text-blue-700">
                          {d.targetSchool}
                        </span>
                        <span className="text-xs text-slate-700 font-medium">{d.targetTrack}</span>
                      </div>
                    </td>
                    <td className="py-3.5 px-4 text-xs font-semibold text-slate-800">
                      {d.declaredOrientationScore ? `${d.declaredOrientationScore}%` : '—'}
                    </td>
                    <td className="py-3.5 px-4 text-xs">
                      {d.releveOcrConfidence ? (
                        <span className="font-semibold text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                          {Math.round(d.releveOcrConfidence * 100)}% validé
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-3.5 px-4 text-xs font-semibold text-slate-800">
                      {d.qcmScore !== undefined ? `${d.qcmScore}/10` : '—'}
                    </td>
                    <td className="py-3.5 px-4">{getStatusBadge(d.status)}</td>
                    <td className="py-3.5 px-4 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => setActiveDossier(d)}
                          title="Consulter la fiche synthétique 1-page"
                          className="p-1.5 text-slate-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors border border-slate-200"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => {
                            setDecisionDossier(d);
                            setDecisionChoice('ADMITTED');
                            setDecisionNotes('');
                          }}
                          className="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold shadow-2xs transition-colors"
                        >
                          Décider
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Modal 1: 1-Page Synthesis Brief (Generated by VISION 1.6 Registrar) */}
      {activeDossier && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between p-5 border-b border-slate-100 bg-slate-50">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-slate-900 text-base">Fiche de Synthèse d’Entretien (1-Page Brief)</h3>
                  <span className="text-[10px] font-mono bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded border border-indigo-200">
                    VISION 1.6 Registrar
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  Dossier {activeDossier.candidateNumber} · {activeDossier.fullName}
                </p>
              </div>
              <button
                onClick={() => setActiveDossier(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-200"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6 space-y-4 max-h-[75vh] overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-slate-50 p-3 rounded-xl border border-slate-200 text-xs">
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">École visée</span>
                  <span className="font-semibold text-slate-900">{activeDossier.targetSchool}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">Score d’orientation</span>
                  <span className="font-semibold text-slate-900">{activeDossier.declaredOrientationScore}%</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">OCR Relevé</span>
                  <span className="font-semibold text-emerald-600">
                    {Math.round((activeDossier.releveOcrConfidence || 0) * 100)}% confiance
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">Score QCM</span>
                  <span className="font-semibold text-slate-900">{activeDossier.qcmScore}/10</span>
                </div>
              </div>

              <div>
                <h4 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">
                  Synthèse du profil générée par l’agent :
                </h4>
                <div className="bg-blue-50/50 p-3.5 rounded-xl border border-blue-100 text-xs text-slate-800 leading-relaxed">
                  {activeDossier.briefSummary || 'Aucune synthèse disponible pour ce profil.'}
                </div>
              </div>

              {activeDossier.interviewSlot && (
                <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-xs flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-blue-600" />
                    <span className="font-medium text-slate-800">
                      Entretien le {activeDossier.interviewSlot.date} à {activeDossier.interviewSlot.startTime}
                    </span>
                  </div>
                  <span className="text-slate-500">Resp. {activeDossier.interviewSlot.responsableName}</span>
                </div>
              )}

              {activeDossier.humanDecision && (
                <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs space-y-1">
                  <div className="font-bold text-emerald-900 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    <span>Décision officielle enregistrée : {activeDossier.humanDecision.decision}</span>
                  </div>
                  <p className="text-emerald-800">Signé par : {activeDossier.humanDecision.decidedBy}</p>
                  <p className="text-slate-600 italic">Notes : {activeDossier.humanDecision.notes}</p>
                </div>
              )}
            </div>

            <div className="p-4 border-t border-slate-100 flex justify-end gap-2 bg-slate-50">
              <button
                onClick={() => setActiveDossier(null)}
                className="px-4 py-2 border border-slate-300 rounded-xl text-xs font-semibold text-slate-700 hover:bg-slate-100"
              >
                Fermer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 2: Human Decision Submission (Admissions HITL) */}
      {decisionDossier && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="p-5 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-slate-900 text-base">Signature de la décision d’admission</h3>
                <p className="text-xs text-slate-500">
                  Candidat : {decisionDossier.fullName} ({decisionDossier.targetSchool} — {decisionDossier.targetTrack})
                </p>
              </div>
              <button
                onClick={() => setDecisionDossier(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleDecisionSubmit} className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                  Verdict Officiel (Human-in-the-Loop)
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { val: 'ADMITTED', label: 'Admettre', color: 'border-emerald-500 bg-emerald-50 text-emerald-800' },
                    { val: 'WAITLIST', label: 'Liste d’attente', color: 'border-amber-500 bg-amber-50 text-amber-800' },
                    { val: 'REJECTED', label: 'Refuser', color: 'border-red-500 bg-red-50 text-red-800' },
                  ].map((opt) => (
                    <button
                      key={opt.val}
                      type="button"
                      onClick={() => setDecisionChoice(opt.val as HumanDecision)}
                      className={`py-2 px-3 rounded-xl border text-xs font-bold transition-all ${
                        decisionChoice === opt.val
                          ? `${opt.color} shadow-xs ring-2 ring-offset-1 ring-blue-500`
                          : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">
                  Justification / Remarques d’entretien
                </label>
                <textarea
                  rows={3}
                  value={decisionNotes}
                  onChange={(e) => setDecisionNotes(e.target.value)}
                  placeholder="Mentionnez vos appréciations suite à l’entretien pédagogique..."
                  required
                  className="w-full p-3 border border-slate-300 rounded-xl text-xs text-slate-900 focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                />
              </div>

              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-[11px] text-slate-500 leading-relaxed">
                Après validation, JARVIS enverra la notification officielle (Email + In-App) au candidat avec les démarches d’inscription administrative.
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setDecisionDossier(null)}
                  className="px-4 py-2 border border-slate-300 rounded-xl text-xs font-semibold text-slate-700 hover:bg-slate-100"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={submittingDecision}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs font-bold shadow-xs transition-colors"
                >
                  {submittingDecision ? 'Enregistrement...' : 'Signer et notifier le candidat'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
