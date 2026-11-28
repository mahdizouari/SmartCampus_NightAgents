'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  CandidateDossier,
  CatalogProgramItem,
  HumanDecision,
  KnowledgeDocumentItem,
  SchoolCode,
  UserSession,
} from '@/types';
import { formatErrorMessage } from '@/lib/api-error';
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
  Database,
  Users,
  FilePlus2,
  BookOpen,
  Edit3,
  RefreshCw,
} from 'lucide-react';

interface ResponsableDashboardProps {
  session?: UserSession;
  onOpenAuth?: () => void;
}

type ActiveTab = 'dossiers' | 'catalog_corpus';

export const ResponsableDashboard: React.FC<ResponsableDashboardProps> = ({
  session,
  onOpenAuth,
}) => {
  const [activeTab, setActiveTab] = useState<ActiveTab>('dossiers');
  const [selectedSchool, setSelectedSchool] = useState<SchoolCode | 'ALL'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  // Tab 1: Candidate Dossiers & HITL Decisions
  const [dossiers, setDossiers] = useState<CandidateDossier[]>([]);
  const [loadingDossiers, setLoadingDossiers] = useState(false);
  const [activeDossier, setActiveDossier] = useState<CandidateDossier | null>(null);
  const [decisionDossier, setDecisionDossier] = useState<CandidateDossier | null>(null);
  const [decisionChoice, setDecisionChoice] = useState<HumanDecision>('ADMITTED');
  const [decisionNotes, setDecisionNotes] = useState('');
  const [submittingDecision, setSubmittingDecision] = useState(false);

  // Tab 2: Academic Catalog & RAG Corpus DB management
  const [programs, setPrograms] = useState<CatalogProgramItem[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocumentItem[]>([]);
  const [loadingBd, setLoadingBd] = useState(false);
  const [bdFeedback, setBdFeedback] = useState<string | null>(null);
  const [bdError, setBdError] = useState<string | null>(null);

  const lockedResponsableSchool: 'iit' | 'isb' | 'isa' | null =
    session?.role === 'RESPONSABLE' && session.school
      ? (session.school.toLowerCase() as 'iit' | 'isb' | 'isa')
      : null;

  // Program Edit Modal State
  const [editingProgram, setEditingProgram] = useState<CatalogProgramItem | null>(null);
  const [progTitle, setProgTitle] = useState('');
  const [progDuration, setProgDuration] = useState(3);
  const [progSource, setProgSource] = useState('');
  const [progDesc, setProgDesc] = useState('');
  const [savingProgram, setSavingProgram] = useState(false);

  // Document Upload Modal State
  const [isDocModalOpen, setIsDocModalOpen] = useState(false);
  const [docCode, setDocCode] = useState('');
  const [docTitle, setDocTitle] = useState('');
  const [docSchool, setDocSchool] = useState<'iit' | 'isb' | 'isa' | 'nau_common'>('iit');
  const [docType, setDocType] = useState('programme');
  const [docDescription, setDocDescription] = useState('');
  const [docContent, setDocContent] = useState('');
  const [docFile, setDocFile] = useState<File | null>(null);
  const [docPublished, setDocPublished] = useState(true);
  const [uploadingDoc, setUploadingDoc] = useState(false);

  const fetchDossiers = useCallback(async (school: SchoolCode | 'ALL') => {
    setLoadingDossiers(true);
    try {
      const data = await responsableService.getDossiers(school === 'ALL' ? undefined : school);
      setDossiers(data);
    } finally {
      setLoadingDossiers(false);
    }
  }, []);

  const refreshBdData = useCallback(async (school: SchoolCode | 'ALL') => {
    setLoadingBd(true);
    try {
      const [progs, docs] = await Promise.all([
        responsableService.getCatalogPrograms(school),
        responsableService.getKnowledgeDocuments(school),
      ]);
      setPrograms(progs);
      setDocuments(docs);
    } finally {
      setLoadingBd(false);
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

  useEffect(() => {
    if (activeTab !== 'catalog_corpus') return;
    let ignore = false;
    Promise.all([
      responsableService.getCatalogPrograms(selectedSchool),
      responsableService.getKnowledgeDocuments(selectedSchool),
    ]).then(([progs, docs]) => {
      if (!ignore) {
        setPrograms(progs);
        setDocuments(docs);
      }
    });
    return () => {
      ignore = true;
    };
  }, [activeTab, selectedSchool]);

  const handleDecisionSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!decisionDossier) return;

    setSubmittingDecision(true);
    try {
      await responsableService.submitHumanDecision({
        dossierId: decisionDossier.id,
        decision: decisionChoice,
        notes: decisionNotes,
        responsableName: session?.fullName || 'Dr. Responsable NAU',
      });
      setDecisionDossier(null);
      setDecisionNotes('');
      await fetchDossiers(selectedSchool);
    } finally {
      setSubmittingDecision(false);
    }
  };

  const openEditProgramModal = (prog: CatalogProgramItem) => {
    setBdError(null);
    setEditingProgram(prog);
    setProgTitle(prog.title);
    setProgDuration(prog.duration_years);
    setProgSource(prog.source_document);
    setProgDesc(prog.description);
  };

  const handleSaveProgram = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingProgram) return;
    setSavingProgram(true);
    setBdFeedback(null);
    setBdError(null);
    try {
      const res = await responsableService.updateCatalogProgram(editingProgram.id, {
        title: progTitle.trim(),
        duration_years: progDuration,
        source_document: progSource.trim(),
        description: progDesc.trim(),
      });
      setPrograms((prev) => prev.map((p) => (p.id === editingProgram.id ? res.item : p)));
      setEditingProgram(null);
      setBdFeedback(
        res.syncedBackend
          ? `Formation ${res.item.code} mise à jour dans PostgreSQL (core.program).`
          : `Formation ${res.item.code} mise à jour en aperçu local (connectez un compte Responsable OTP pour persister en base).`,
      );
    } catch (err: unknown) {
      setBdError(formatErrorMessage(err));
    } finally {
      setSavingProgram(false);
    }
  };

  const handleUploadDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanedCode = docCode.trim();
    const cleanedTitle = docTitle.trim();
    if (!cleanedCode || !cleanedTitle) return;
    if (cleanedCode.length < 2 || !/^[A-Za-z0-9._-]+$/.test(cleanedCode)) {
      setBdError(
        'Le code document doit comporter au moins 2 caractères (lettres, chiffres, points, tirets ou underscores uniquement).',
      );
      return;
    }
    if (!docFile && !docContent.trim()) {
      setBdError('Veuillez joindre un fichier (PDF/MD/TXT/DOCX) ou saisir un contenu Markdown.');
      return;
    }
    const effectiveSchool = lockedResponsableSchool || docSchool;
    setUploadingDoc(true);
    setBdFeedback(null);
    setBdError(null);
    try {
      const res = await responsableService.uploadKnowledgeDocument({
        code: cleanedCode,
        title: cleanedTitle,
        school_code: effectiveSchool,
        doc_type: docType,
        description: docDescription,
        is_published: docPublished,
        file: docFile,
        content: docContent,
      });
      setDocuments((prev) => [
        res.item,
        ...prev.filter((d) => d.code !== res.item.code),
      ]);
      setIsDocModalOpen(false);
      setDocCode('');
      setDocTitle('');
      setDocDescription('');
      setDocContent('');
      setDocFile(null);
      setBdFeedback(
        res.syncedBackend
          ? `Document officiel ${res.item.code} enregistré et mis en file d’indexation RAG (SA-56).`
          : `Document ${res.item.code} ajouté en aperçu local (connectez un compte Responsable OTP pour persister dans knowledge.corpus_documents).`,
      );
    } catch (err: unknown) {
      setBdError(formatErrorMessage(err));
    } finally {
      setUploadingDoc(false);
    }
  };

  const filteredDossiers = dossiers.filter((d) => {
    const q = searchQuery.toLowerCase();
    return (
      d.fullName.toLowerCase().includes(q) ||
      d.candidateNumber.toLowerCase().includes(q) ||
      d.targetTrack.toLowerCase().includes(q)
    );
  });

  const filteredPrograms = programs.filter((p) => {
    const q = searchQuery.toLowerCase();
    return (
      p.code.toLowerCase().includes(q) ||
      p.title.toLowerCase().includes(q) ||
      p.degree_level.toLowerCase().includes(q)
    );
  });

  const filteredDocuments = documents.filter((d) => {
    const q = searchQuery.toLowerCase();
    return (
      d.code.toLowerCase().includes(q) ||
      d.title.toLowerCase().includes(q) ||
      d.doc_type.toLowerCase().includes(q)
    );
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'ADMITTED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <CheckCircle className="w-3 h-3" /> Admis (Signé)
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
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900">Portail Responsable NAU</h1>
            <span className="px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 text-xs font-bold border border-emerald-200 flex items-center gap-1">
              <ShieldCheck className="w-3.5 h-3.5" />
              Décision Humaine &amp; Suivi BD
            </span>
          </div>
          <p className="text-sm text-slate-700 mt-1 font-medium">
            Supervision des candidatures IIT / ISB / ISA, signature officielle des décisions
            d’admission et mise à jour directe du catalogue et du corpus RAG.
          </p>
        </div>

        {/* HITL Rule reminder box */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-start gap-2.5 max-w-md">
          <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-amber-900 leading-snug font-medium">
            <strong>Règle d’Or SmartCampus :</strong> Aucun agent IA n’admet ou ne refuse un
            Candidat. Le Responsable supervise la base officielle et signe seul la décision finale.
          </p>
        </div>
      </div>

      {/* Staff Authentication Banner if currently in Visitor / Candidate mode */}
      {(!session || session.role !== 'RESPONSABLE') && onOpenAuth && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 flex flex-wrap items-center justify-between gap-3 text-xs text-blue-900">
          <span>
            Vous consultez le portail en mode aperçu. Connectez-vous en tant que{' '}
            <strong>Responsable</strong> pour persister vos mises à jour catalogue / RAG en base PostgreSQL.
          </span>
          <button
            type="button"
            onClick={onOpenAuth}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold transition-colors"
          >
            Identification Responsable
          </button>
        </div>
      )}

      {/* Workspace Tabs: Candidate Dossiers (HITL) vs Academic Catalog & RAG Corpus */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div className="flex items-center gap-2 bg-slate-100 p-1 rounded-xl">
          <button
            type="button"
            onClick={() => setActiveTab('dossiers')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'dossiers'
                ? 'bg-white text-blue-700 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Users className="w-4 h-4" />
            <span>Dossiers Candidats &amp; Décisions HITL ({dossiers.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('catalog_corpus')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'catalog_corpus'
                ? 'bg-white text-emerald-700 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Database className="w-4 h-4" />
            <span>Catalogue &amp; Corpus BD (Update / Suivi BD)</span>
          </button>
        </div>

        {activeTab === 'catalog_corpus' && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refreshBdData(selectedSchool)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-slate-200 hover:bg-slate-50 rounded-lg text-xs font-semibold text-slate-700"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingBd ? 'animate-spin' : ''}`} />
              <span>Actualiser BD</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setBdError(null);
                setDocSchool(
                  lockedResponsableSchool
                    ? lockedResponsableSchool
                    : selectedSchool === 'ALL'
                    ? 'iit'
                    : (selectedSchool.toLowerCase() as 'iit' | 'isb' | 'isa'),
                );
                setIsDocModalOpen(true);
              }}
              className="flex items-center gap-1.5 px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-bold shadow-xs transition-colors"
            >
              <FilePlus2 className="w-3.5 h-3.5" />
              <span>Ajouter un document officiel RAG</span>
            </button>
          </div>
        )}
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-2xs flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-lg w-full sm:w-auto">
          {(['ALL', 'IIT', 'ISB', 'ISA'] as const).map((school) => (
            <button
              key={school}
              type="button"
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

        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
          <input
            type="text"
            placeholder={
              activeTab === 'dossiers'
                ? 'Rechercher candidat, n° dossier, filière...'
                : 'Filtrer code formation, titre document RAG...'
            }
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          />
        </div>
      </div>

      {bdFeedback && activeTab === 'catalog_corpus' && (
        <div className="bg-emerald-50 border border-emerald-200 text-emerald-900 px-4 py-2.5 rounded-xl text-xs font-medium flex items-center justify-between">
          <span>{bdFeedback}</span>
          <button
            type="button"
            onClick={() => setBdFeedback(null)}
            className="text-emerald-700 hover:text-emerald-900"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {bdError && activeTab === 'catalog_corpus' && (
        <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-2.5 rounded-xl text-xs font-medium flex items-center justify-between">
          <span>{bdError}</span>
          <button
            type="button"
            onClick={() => setBdError(null)}
            className="text-red-700 hover:text-red-900"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* TAB 1: Candidate Dossiers & HITL Decisions */}
      {activeTab === 'dossiers' ? (
        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                  <th className="py-3 px-4">Candidat</th>
                  <th className="py-3 px-4">École &amp; Filière</th>
                  <th className="py-3 px-4">Score Orientation</th>
                  <th className="py-3 px-4">Relevé (VLM)</th>
                  <th className="py-3 px-4">QCM</th>
                  <th className="py-3 px-4">Statut</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-sm">
                {loadingDossiers ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-slate-400 text-xs">
                      Chargement des dossiers candidats...
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
                        <div className="text-[11px] text-slate-500 font-mono">
                          {d.candidateNumber}
                        </div>
                      </td>
                      <td className="py-3.5 px-4">
                        <div className="flex items-center gap-1.5">
                          <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-blue-100 text-blue-700">
                            {d.targetSchool}
                          </span>
                          <span className="text-xs text-slate-700 font-medium">
                            {d.targetTrack}
                          </span>
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
                            type="button"
                            onClick={() => setActiveDossier(d)}
                            title="Consulter la fiche synthétique 1-page"
                            className="p-1.5 text-slate-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors border border-slate-200"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
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
      ) : (
        /* TAB 2: Academic Catalog & RAG Corpus DB Management */
        <div className="space-y-6">
          {/* Section A: Official RAG Corpus Documents */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
            <div className="px-5 py-3.5 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-emerald-600" />
                <h2 className="text-sm font-bold text-slate-900">
                  Corpus Documentaire Officiel RAG (core.documents · pgvector bge-m3 1024d)
                </h2>
              </div>
              <span className="text-xs font-semibold text-slate-600">
                {filteredDocuments.length} document(s)
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-50/60 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                    <th className="py-2.5 px-4">Code</th>
                    <th className="py-2.5 px-4">Titre Officiel</th>
                    <th className="py-2.5 px-4">École</th>
                    <th className="py-2.5 px-4">Catégorie</th>
                    <th className="py-2.5 px-4">Version</th>
                    <th className="py-2.5 px-4">Statut Index RAG</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-xs">
                  {loadingBd ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-slate-400">
                        Synchronisation du corpus documentaire...
                      </td>
                    </tr>
                  ) : filteredDocuments.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-slate-400">
                        Aucun document officiel trouvé pour ce filtre.
                      </td>
                    </tr>
                  ) : (
                    filteredDocuments.map((doc) => (
                      <tr key={doc.id} className="hover:bg-slate-50/70">
                        <td className="py-3 px-4 font-mono font-semibold text-slate-800">
                          {doc.code}
                        </td>
                        <td className="py-3 px-4">
                          <div className="font-semibold text-slate-900">{doc.title}</div>
                          {doc.description && (
                            <div className="text-[11px] text-slate-500 line-clamp-1">
                              {doc.description}
                            </div>
                          )}
                        </td>
                        <td className="py-3 px-4">
                          <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700 font-bold uppercase text-[10px] border border-blue-200">
                            {doc.school_code}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-slate-600">{doc.doc_type}</td>
                        <td className="py-3 px-4 font-mono text-slate-700">
                          v{doc.latest_version_number || 1}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                              doc.index_status === 'SYNCED' || doc.index_status === 'INDEXED'
                                ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                                : doc.index_status === 'STALE'
                                ? 'bg-amber-50 text-amber-800 border-amber-200'
                                : 'bg-blue-50 text-blue-800 border-blue-200'
                            }`}
                          >
                            {doc.index_status} · {doc.is_published ? 'PUBLIÉ' : doc.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section B: Academic Programs Catalog */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
            <div className="px-5 py-3.5 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Database className="w-4 h-4 text-blue-600" />
                <h2 className="text-sm font-bold text-slate-900">
                  Catalogue des Formations &amp; Sources Officielles (core.program)
                </h2>
              </div>
              <span className="text-xs font-semibold text-slate-600">
                {filteredPrograms.length} formation(s)
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-50/60 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                    <th className="py-2.5 px-4">Code</th>
                    <th className="py-2.5 px-4">Intitulé de la Formation</th>
                    <th className="py-2.5 px-4">Cycle</th>
                    <th className="py-2.5 px-4">Durée</th>
                    <th className="py-2.5 px-4">Source Officielle</th>
                    <th className="py-2.5 px-4 text-right">Mise à jour</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-xs">
                  {loadingBd ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-slate-400">
                        Chargement du catalogue PostgreSQL...
                      </td>
                    </tr>
                  ) : filteredPrograms.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-slate-400">
                        Aucune formation trouvée pour ce filtre.
                      </td>
                    </tr>
                  ) : (
                    filteredPrograms.map((prog) => (
                      <tr key={prog.id} className="hover:bg-slate-50/70">
                        <td className="py-3 px-4 font-mono font-semibold text-slate-800">
                          <span className="px-1.5 py-0.5 mr-1.5 rounded bg-slate-100 text-slate-700 text-[10px] uppercase">
                            {prog.school_code}
                          </span>
                          {prog.code}
                        </td>
                        <td className="py-3 px-4">
                          <div className="font-semibold text-slate-900">{prog.title}</div>
                          <div className="text-[11px] text-slate-500 line-clamp-1">
                            {prog.description}
                          </div>
                        </td>
                        <td className="py-3 px-4 text-slate-700 font-medium">
                          {prog.degree_level}
                        </td>
                        <td className="py-3 px-4 text-slate-700">{prog.duration_years} ans</td>
                        <td className="py-3 px-4 text-[11px] text-blue-700 truncate max-w-52">
                          {prog.source_document}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            type="button"
                            onClick={() => openEditProgramModal(prog)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 bg-slate-100 hover:bg-blue-50 hover:text-blue-700 text-slate-700 border border-slate-200 rounded-lg font-semibold transition-colors"
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                            <span>Modifier</span>
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Modal 1: 1-Page Synthesis Brief */}
      {activeDossier && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between p-5 border-b border-slate-100 bg-slate-50">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-slate-900 text-base">
                    Fiche de Synthèse d’Entretien (1-Page Brief)
                  </h3>
                  <span className="text-[10px] font-mono bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded border border-indigo-200">
                    VISION 1.6 Registrar
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  Dossier {activeDossier.candidateNumber} · {activeDossier.fullName}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setActiveDossier(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-200"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6 space-y-4 max-h-[75vh] overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-slate-50 p-3 rounded-xl border border-slate-200 text-xs">
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">
                    École visée
                  </span>
                  <span className="font-semibold text-slate-900">{activeDossier.targetSchool}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">
                    Score d’orientation
                  </span>
                  <span className="font-semibold text-slate-900">
                    {activeDossier.declaredOrientationScore}%
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">
                    Relevé VLM
                  </span>
                  <span className="font-semibold text-emerald-600">
                    {Math.round((activeDossier.releveOcrConfidence || 0) * 100)}% confiance
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold">
                    Score QCM
                  </span>
                  <span className="font-semibold text-slate-900">{activeDossier.qcmScore}/10</span>
                </div>
              </div>

              <div>
                <h4 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">
                  Synthèse du profil Candidat :
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
                      Entretien le {activeDossier.interviewSlot.date} à{' '}
                      {activeDossier.interviewSlot.startTime}
                    </span>
                  </div>
                  <span className="text-slate-500">
                    Resp. {activeDossier.interviewSlot.responsableName}
                  </span>
                </div>
              )}

              {activeDossier.humanDecision && (
                <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs space-y-1">
                  <div className="font-bold text-emerald-900 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    <span>
                      Décision officielle enregistrée : {activeDossier.humanDecision.decision}
                    </span>
                  </div>
                  <p className="text-emerald-800">
                    Signé par : {activeDossier.humanDecision.decidedBy}
                  </p>
                  <p className="text-slate-600 italic">
                    Notes : {activeDossier.humanDecision.notes}
                  </p>
                </div>
              )}
            </div>

            <div className="p-4 border-t border-slate-100 flex justify-end gap-2 bg-slate-50">
              <button
                type="button"
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
                <h3 className="font-bold text-slate-900 text-base">
                  Signature de la décision d’admission
                </h3>
                <p className="text-xs text-slate-500">
                  Candidat : {decisionDossier.fullName} ({decisionDossier.targetSchool} —{' '}
                  {decisionDossier.targetTrack})
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDecisionDossier(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleDecisionSubmit} className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                  Verdict Officiel du Responsable (Human-in-the-Loop)
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    {
                      val: 'ADMITTED',
                      label: 'Admettre',
                      color: 'border-emerald-500 bg-emerald-50 text-emerald-800',
                    },
                    {
                      val: 'WAITLIST',
                      label: 'Liste d’attente',
                      color: 'border-amber-500 bg-amber-50 text-amber-800',
                    },
                    {
                      val: 'REJECTED',
                      label: 'Refuser',
                      color: 'border-red-500 bg-red-50 text-red-800',
                    },
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
                  {submittingDecision ? 'Enregistrement...' : 'Signer et notifier le Candidat'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal 3: Upload Official Knowledge Document (POST /api/v1/knowledge/documents) */}
      {isDocModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-lg overflow-hidden">
            <div className="p-5 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  Ajouter un Document Officiel au Corpus RAG
                </h3>
                <p className="text-xs text-slate-500">
                  Indexation versionnée SHA-256 (SA-49 / SA-56) pilotée par le Responsable
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsDocModalOpen(false)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleUploadDocument} className="p-5 space-y-3.5 text-xs">
              {bdError && (
                <div className="p-2.5 rounded-xl bg-red-50 border border-red-200 text-red-800 font-medium">
                  {bdError}
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">
                    Code Document
                  </label>
                  <input
                    type="text"
                    required
                    minLength={2}
                    maxLength={64}
                    pattern="^[A-Za-z0-9._-]+$"
                    title="2 à 64 caractères : lettres, chiffres, points, tirets ou underscores"
                    value={docCode}
                    onChange={(e) => setDocCode(e.target.value)}
                    placeholder="IIT-GLID-2027"
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                  />
                </div>
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">
                    École {lockedResponsableSchool ? `(${session?.school})` : ''}
                  </label>
                  <select
                    value={lockedResponsableSchool || docSchool}
                    disabled={Boolean(lockedResponsableSchool)}
                    onChange={(e) =>
                      setDocSchool(e.target.value as 'iit' | 'isb' | 'isa' | 'nau_common')
                    }
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900 bg-white disabled:bg-slate-100 disabled:text-slate-600 disabled:cursor-not-allowed"
                  >
                    <option value="iit">IIT</option>
                    <option value="isb">ISB</option>
                    <option value="isa">ISA</option>
                    <option value="nau_common">NAU Commun</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">
                    Titre Officiel
                  </label>
                  <input
                    type="text"
                    required
                    minLength={3}
                    maxLength={255}
                    value={docTitle}
                    onChange={(e) => setDocTitle(e.target.value)}
                    placeholder="Brochure Génie Informatique IIT"
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                  />
                </div>
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">Catégorie</label>
                  <select
                    value={docType}
                    onChange={(e) => setDocType(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900 bg-white"
                  >
                    <option value="programme">Programme / Filière</option>
                    <option value="admission_rules">Règles d’admission</option>
                    <option value="fees_schedule">Grille tarifaire</option>
                    <option value="equivalence_grid">Grille passerelles</option>
                    <option value="general_brochure">Brochure générale</option>
                    <option value="reglement">Règlement intérieur</option>
                    <option value="faq">FAQ officielle</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 uppercase mb-1">
                  Description courte
                </label>
                <input
                  type="text"
                  value={docDescription}
                  onChange={(e) => setDocDescription(e.target.value)}
                  placeholder="Périmètre et année universitaire..."
                  className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                />
              </div>

              <div>
                <label className="block font-bold text-slate-700 uppercase mb-1">
                  Fichier officiel (PDF, Markdown, TXT, DOCX)
                </label>
                <input
                  type="file"
                  accept=".pdf,.md,.txt,.docx"
                  onChange={(e) => setDocFile(e.target.files?.[0] || null)}
                  className="w-full text-slate-700 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-blue-50 file:text-blue-700 file:font-semibold"
                />
              </div>

              <div>
                <label className="block font-bold text-slate-700 uppercase mb-1">
                  Ou saisie directe Markdown (si aucun fichier joint)
                </label>
                <textarea
                  rows={3}
                  value={docContent}
                  onChange={(e) => setDocContent(e.target.value)}
                  placeholder="# Contenu officiel vérifié..."
                  className="w-full p-2.5 border border-slate-300 rounded-xl text-slate-900 font-mono"
                />
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={docPublished}
                  onChange={(e) => setDocPublished(e.target.checked)}
                />
                <span className="font-medium text-slate-700">
                  Publier immédiatement pour l’agent VISION 1.2_Librarian
                </span>
              </label>

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setIsDocModalOpen(false)}
                  className="px-4 py-2 border border-slate-300 rounded-xl font-semibold text-slate-700"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={uploadingDoc}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold"
                >
                  {uploadingDoc ? 'Enregistrement...' : 'Enregistrer dans le corpus'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal 4: Edit Catalog Program (PATCH /api/v1/catalog/programs/{id}) */}
      {editingProgram && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-lg overflow-hidden">
            <div className="p-5 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-slate-900 text-base">
                  Mettre à jour la formation ({editingProgram.code})
                </h3>
                <p className="text-xs text-slate-500">
                  Mise à jour directe de core.program par le Responsable ({editingProgram.school_code.toUpperCase()})
                </p>
              </div>
              <button
                type="button"
                onClick={() => setEditingProgram(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveProgram} className="p-5 space-y-3.5 text-xs">
              {bdError && (
                <div className="p-2.5 rounded-xl bg-red-50 border border-red-200 text-red-800 font-medium">
                  {bdError}
                </div>
              )}
              <div>
                <label className="block font-bold text-slate-700 uppercase mb-1">
                  Intitulé officiel
                </label>
                <input
                  type="text"
                  required
                  value={progTitle}
                  onChange={(e) => setProgTitle(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">
                    Durée (années)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={6}
                    required
                    value={progDuration}
                    onChange={(e) => setProgDuration(Number(e.target.value))}
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                  />
                </div>
                <div>
                  <label className="block font-bold text-slate-700 uppercase mb-1">
                    Source officielle (URL / Brochure)
                  </label>
                  <input
                    type="text"
                    required
                    value={progSource}
                    onChange={(e) => setProgSource(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded-xl text-slate-900"
                  />
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 uppercase mb-1">
                  Description pédagogique
                </label>
                <textarea
                  rows={3}
                  value={progDesc}
                  onChange={(e) => setProgDesc(e.target.value)}
                  className="w-full p-2.5 border border-slate-300 rounded-xl text-slate-900"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setEditingProgram(null)}
                  className="px-4 py-2 border border-slate-300 rounded-xl font-semibold text-slate-700"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={savingProgram}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-bold"
                >
                  {savingProgram ? 'Sauvegarde...' : 'Enregistrer en BD'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
