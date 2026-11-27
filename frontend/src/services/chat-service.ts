import { apiClient } from '@/lib/api-client';
import { normalizeRoleToActorStage } from '@/services/auth-service';
import {
  ActorStage,
  CampusScope,
  CandidateStatus,
  ChatActionPrompt,
  ChatMessage,
  TrackRecommendation,
  VisionSubAgentId,
} from '@/types';
import { INITIAL_MESSAGES } from '@/lib/mock-data';

export interface SendMessagePayload {
  sessionId?: string;
  session_id?: string;
  message: string;
  isVoice?: boolean;
  campus_scope?: CampusScope;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
}

export interface ChatResponse {
  sessionId: string;
  session_id?: string;
  reply: string;
  reply_text?: string;
  subAgent: VisionSubAgentId;
  sub_agent?: VisionSubAgentId;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
  recommendations?: TrackRecommendation[];
  actionPrompt?: ChatActionPrompt;
  actor_stage?: ActorStage | string;
  candidate_status?: CandidateStatus;
  citations?: {
    id: string;
    documentTitle: string;
    source_document?: string;
    section?: string;
    snippet?: string;
  }[];
}

class ChatService {
  private memoryHistory: ChatMessage[] = [...INITIAL_MESSAGES];

  public getLocalHistory(): ChatMessage[] {
    return this.memoryHistory;
  }

  public appendMessage(msg: ChatMessage): void {
    this.memoryHistory.push(msg);
  }

  public async sendMessage(payload: SendMessagePayload): Promise<ChatMessage> {
    const activeGuestSid = payload.sessionId || apiClient.getStoredGuestSessionId() || undefined;
    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      content: payload.message,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      isVoice: payload.isVoice,
      detectedDialect: payload.detectedDialect,
    };
    this.memoryHistory.push(userMsg);

    try {
      const res = await apiClient.post<ChatResponse>('/chat/message', {
        ...payload,
        sessionId: activeGuestSid,
        session_id: activeGuestSid,
      });

      const botMsg: ChatMessage = {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: res.subAgent || res.sub_agent,
        content: res.reply || res.reply_text || '',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        detectedDialect: res.detectedDialect,
        recommendations: res.recommendations,
        citations: res.citations?.map((c) => ({
          id: c.id,
          documentTitle: c.documentTitle || c.source_document || 'Corpus Officiel NAU',
          section: c.section,
          snippet: c.snippet,
        })),
        actionPrompt: res.actionPrompt,
        actorStage: res.actor_stage
          ? normalizeRoleToActorStage(res.actor_stage, false)
          : undefined,
        candidateStatus: res.candidate_status,
      };
      this.memoryHistory.push(botMsg);
      return botMsg;
    } catch {
      const botMsg = this.generateFallbackResponse(payload.message);
      this.memoryHistory.push(botMsg);
      return botMsg;
    }
  }

  public generateFallbackResponse(userText: string): ChatMessage {
    const lower = userText.toLowerCase();
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const isArabizi = /[0-9]/.test(userText) && /(3aslema|chnowa|nheb|kifech|fama)/i.test(userText);
    const isDerja = /(شنوة|نحب|كيفاش|برنامج)/.test(userText);

    if (
      lower.includes('informatique') ||
      lower.includes('software') ||
      lower.includes('code') ||
      lower.includes('info') ||
      isArabizi
    ) {
      return {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: '1.5_Advisor',
        detectedDialect: isArabizi ? 'Arabizi' : isDerja ? 'Tunisian_Derja' : 'French',
        candidateStatus: 'ORIENTED',
        content: isArabizi
          ? "Mar7ba bik! Hasb l'profil li qolthouli, fama zouz parcours ywalmou l'candidat fi l'IIT w l'ISB :"
          : 'Selon les intérêts exprimés en ingénierie et technologies numériques, voici 2 filières adaptées au sein du campus NAU :',
        timestamp: timeStr,
        citations: [
          {
            id: 'c1',
            documentTitle: 'Portail Officiel IIT Sfax (iit.tn/formation)',
            section: 'Cycle Ingénieur — Génie Informatique',
            snippet:
              'Formation d’ingénieurs couvrant le génie logiciel, la science des données, l’IA et les réseaux.',
          },
        ],
        recommendations: [
          {
            trackCode: 'IIT-ING-GLID',
            trackName: 'Cycle Ingénieur en Génie Informatique (IIT)',
            school: 'IIT',
            compatibilityScore: 94,
            matchingStrengths: ['Algorithmique', 'Intelligence Artificielle', 'Architecture Logicielle'],
            justification:
              'Forte adéquation avec un profil scientifique orienté conception logicielle et systèmes intelligents.',
          },
          {
            trackCode: 'ISB-LIC-BUSINESS',
            trackName: 'Licence Administration des Affaires (ISB)',
            school: 'ISB',
            compatibilityScore: 78,
            matchingStrengths: ['Management Technologique', 'Stratégie Digitale'],
            justification:
              'Parcours pertinent pour combiner gestion d’entreprise et transformation numérique.',
          },
        ],
        actionPrompt: 'CONFIRM_SHORTLIST',
      };
    }

    if (lower.includes('releve') || lower.includes('relevé') || lower.includes('note') || lower.includes('inscription')) {
      return {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: '1.4_Reader',
        content:
          'Conformément au principe « Give or Keep », l’orientation en mode Visiteur est libre, mais le passage en mode Candidat (connexion Email OTP) est requis avant de téléverser un relevé de notes officiel ou de soumettre une préinscription. Votre échange actuel sera fusionné automatiquement.',
        timestamp: timeStr,
        actionPrompt: 'AUTH_REQUIRED',
      };
    }

    return {
      id: `vis-${Date.now()}`,
      sender: 'vision',
      subAgent: '1.2_Librarian',
      candidateStatus: 'PROFILING',
      content:
        'J’ai bien noté votre demande. À l’IIT (Prépa, Licences, Cycle Ingénieur) et à l’ISB (Licences et Masters en Gestion/Finance), l’admission repose sur l’étude du dossier suivie d’un entretien validé exclusivement par le Responsable pédagogique. Que vous soyez futur candidat ou parent d’élève, précisez le diplôme actuel (Bac, Licence, Prépa) pour vérifier l’éligibilité !',
      timestamp: timeStr,
      citations: [
        {
          id: 'c0',
          documentTitle: 'Règlement Officiel des Admissions NAU',
          section: 'Conditions d’éligibilité et décision humaine Responsable',
        },
      ],
    };
  }
}

export const chatService = new ChatService();
