'use client';

import React, { useState } from 'react';
import { X, Mail, KeyRound, ArrowRight, ShieldCheck, CheckCircle2, User } from 'lucide-react';
import { formatErrorMessage } from '@/lib/api-error';
import { authService } from '@/services/auth-service';
import { UserSession } from '@/types';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (session: UserSession) => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [acceptConsent, setAcceptConsent] = useState(true);
  const [step, setStep] = useState<'EMAIL' | 'OTP'>('EMAIL');
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !email.includes('@')) {
      setErrorMsg('Veuillez saisir une adresse email valide.');
      return;
    }
    if (!acceptConsent) {
      setErrorMsg('Veuillez accepter le consentement INPDP (Loi n° 2004-63) pour devenir Candidat.');
      return;
    }
    setLoading(true);
    setFeedback(null);
    setErrorMsg(null);
    try {
      const res = await authService.requestEmailOtp(email);
      setFeedback(res.message);
      if (res.devOtpCode) {
        setCode(res.devOtpCode);
      }
      setStep('OTP');
    } catch (err: unknown) {
      setErrorMsg(formatErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code || code.trim().length !== 6) {
      setErrorMsg('Veuillez saisir le code à 6 chiffres reçu.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const session = await authService.verifyEmailOtp(email, code.trim(), {
        fullName,
        acceptConsent,
      });
      onSuccess(session);
      onClose();
    } catch (err: unknown) {
      setErrorMsg(formatErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleResponsableDemoLogin = async () => {
    setLoading(true);
    setErrorMsg(null);
    setFeedback(null);
    try {
      const session = await authService.loginAsResponsableDemo('IIT');
      onSuccess(session);
      onClose();
    } catch (err: unknown) {
      setErrorMsg(formatErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-md overflow-hidden relative animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-slate-100">
          <div>
            <h3 className="font-bold text-slate-900 text-lg">Passage Visiteur → Candidat</h3>
            <p className="text-xs text-slate-700 font-medium">
              Identification par code OTP &amp; fusion automatique de votre session
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Informative Banner (Principle Give or Keep) */}
        <div className="bg-blue-50/70 p-3.5 mx-5 mt-4 rounded-xl border border-blue-100 flex items-start gap-2.5">
          <CheckCircle2 className="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-blue-900 leading-relaxed font-medium">
            <strong>Transition Visiteur → Candidat :</strong> Votre historique d’orientation anonyme
            et vos préférences sont associés automatiquement à votre dossier Candidat dès validation
            du code OTP, débloquant le téléversement VLM du relevé de notes.
          </p>
        </div>

        {/* Content */}
        <div className="p-5">
          {step === 'EMAIL' ? (
            <form onSubmit={handleRequestOtp} className="space-y-3.5">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase mb-1">
                  Nom complet du Candidat (optionnel)
                </label>
                <div className="relative">
                  <User className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Ex. : Amine Ben Salah"
                    className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-xl text-sm focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-slate-900"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase mb-1">
                  Adresse Email du Candidat (ou du parent référent)
                </label>
                <div className="relative">
                  <Mail className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="prenom.nom@candidat.tn"
                    required
                    className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-xl text-sm focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-slate-900"
                  />
                </div>
              </div>

              <label className="flex items-start gap-2.5 p-2.5 bg-slate-50 border border-slate-200 rounded-xl cursor-pointer">
                <input
                  type="checkbox"
                  checked={acceptConsent}
                  onChange={(e) => setAcceptConsent(e.target.checked)}
                  className="mt-0.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-[11px] text-slate-700 leading-snug">
                  Consentement explicite <strong>INPDP (Loi n° 2004-63)</strong> : j’autorise la
                  fusion de ma session d’orientation et l’analyse OCR/VLM de mon relevé officiel
                  pour ma candidature NAU.
                </span>
              </label>

              {errorMsg && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 p-2.5 rounded-lg">
                  {errorMsg}
                </div>
              )}
              {feedback && (
                <div className="text-xs text-blue-700 bg-blue-50 p-2.5 rounded-lg">{feedback}</div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold shadow-md shadow-blue-500/15 transition-all disabled:opacity-50"
              >
                <span>{loading ? 'Envoi en cours...' : 'Recevoir mon code Candidat (OTP)'}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          ) : (
            <form onSubmit={handleVerifyOtp} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase mb-1">
                  Code de vérification (6 chiffres)
                </label>
                <div className="relative">
                  <KeyRound className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="text"
                    maxLength={6}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="123456"
                    required
                    className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-xl text-sm tracking-widest font-mono text-center focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-slate-900 font-bold"
                  />
                </div>
                <p className="text-[11px] text-slate-700 mt-1 font-medium">
                  Code envoyé à <strong>{email}</strong>
                </p>
              </div>

              {feedback && (
                <div className="text-xs text-blue-700 bg-blue-50 p-2.5 rounded-lg">{feedback}</div>
              )}
              {errorMsg && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 p-2.5 rounded-lg">
                  {errorMsg}
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold shadow-md shadow-blue-500/15 transition-all disabled:opacity-50"
              >
                <span>
                  {loading ? 'Vérification & fusion...' : 'Activer mon espace Candidat'}
                </span>
                <ArrowRight className="w-4 h-4" />
              </button>

              <button
                type="button"
                onClick={() => {
                  setStep('EMAIL');
                  setErrorMsg(null);
                }}
                className="w-full text-center text-xs text-slate-500 hover:text-slate-800"
              >
                Modifier l’adresse email
              </button>
            </form>
          )}

          {/* Quick Responsable Demo Login shortcut */}
          <div className="mt-5 pt-4 border-t border-slate-100">
            <p className="text-[11px] text-slate-700 text-center mb-2 font-medium">
              Accès démonstration encadrement académique
            </p>
            <button
              onClick={() => void handleResponsableDemoLogin()}
              disabled={loading}
              type="button"
              className="w-full flex items-center justify-center gap-2 py-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 rounded-xl text-xs font-semibold transition-colors"
            >
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
              <span>Connexion Démo : Responsable IIT (HITL &amp; Suivi BD)</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
