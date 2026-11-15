'use client';

import React, { useState } from 'react';
import { X, Mail, KeyRound, ArrowRight, ShieldCheck, CheckCircle2 } from 'lucide-react';
import { authService } from '@/services/auth-service';
import { UserSession } from '@/types';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (session: UserSession) => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'EMAIL' | 'OTP'>('EMAIL');
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !email.includes('@')) {
      setFeedback('Veuillez entrer une adresse email valide.');
      return;
    }
    setLoading(true);
    setFeedback(null);
    try {
      const res = await authService.requestEmailOtp(email);
      setFeedback(res.message);
      setStep('OTP');
    } catch {
      setFeedback('Erreur lors de l’envoi du code. Veuillez réessayer.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code) {
      setFeedback('Veuillez saisir le code reçu.');
      return;
    }
    setLoading(true);
    setFeedback(null);
    try {
      const session = await authService.verifyEmailOtp(email, code);
      onSuccess(session);
      onClose();
    } catch {
      setFeedback('Code invalide ou expiré.');
    } finally {
      setLoading(false);
    }
  };

  const handleStaffDemoLogin = () => {
    const session = authService.loginAsStaff('RESPONSABLE', 'IIT');
    onSuccess(session);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-md overflow-hidden relative animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-slate-100">
          <div>
            <h3 className="font-bold text-slate-900 text-lg">Espace Candidat NAU</h3>
            <p className="text-xs text-slate-700 font-medium">Authentification sécurisée par Email OTP</p>
          </div>
          <button
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
            <strong>Principe « Give or Keep » :</strong> Votre historique de chat et vos filières sélectionnées sont automatiquement fusionnés lors de la connexion.
          </p>
        </div>

        {/* Content */}
        <div className="p-5">
          {step === 'EMAIL' ? (
            <form onSubmit={handleRequestOtp} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase mb-1">
                  Adresse Email
                </label>
                <div className="relative">
                  <Mail className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="exemple@etudiant.tn"
                    required
                    className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-xl text-sm focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-slate-900"
                  />
                </div>
              </div>

              {feedback && <div className="text-xs text-blue-700 bg-blue-50 p-2.5 rounded-lg">{feedback}</div>}

              <button
                type="submit"
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold shadow-md shadow-blue-500/15 transition-all disabled:opacity-50"
              >
                <span>{loading ? 'Envoi en cours...' : 'Recevoir mon code par email'}</span>
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
                <p className="text-[11px] text-slate-700 mt-1 font-medium">Code envoyé à <strong>{email}</strong></p>
              </div>

              {feedback && <div className="text-xs text-blue-700 bg-blue-50 p-2.5 rounded-lg">{feedback}</div>}

              <button
                type="submit"
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold shadow-md shadow-blue-500/15 transition-all disabled:opacity-50"
              >
                <span>{loading ? 'Vérification...' : 'Valider et continuer'}</span>
                <ArrowRight className="w-4 h-4" />
              </button>

              <button
                type="button"
                onClick={() => setStep('EMAIL')}
                className="w-full text-center text-xs text-slate-500 hover:text-slate-800"
              >
                Changer d’adresse email
              </button>
            </form>
          )}

          {/* Quick Staff Login shortcut for demo/testing */}
          <div className="mt-6 pt-4 border-t border-slate-100">
            <p className="text-[11px] text-slate-700 text-center mb-2 font-medium">Accès démonstration équipe</p>
            <button
              onClick={handleStaffDemoLogin}
              type="button"
              className="w-full flex items-center justify-center gap-2 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-semibold transition-colors"
            >
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
              <span>Connexion Démo : Responsable Pédagogique IIT</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

