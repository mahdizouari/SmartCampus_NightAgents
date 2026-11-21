import { apiClient } from '@/lib/api-client';
import { ChatMessage, TrackRecommendation } from '@/types';
import { INITIAL_MESSAGES } from '@/lib/mock-data';

export interface SendMessagePayload {
  sessionId?: string;
  message: string;
  isVoice?: boolean;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
}

export interface ChatResponse {
  sessionId: string;
  reply: string;
  subAgent: '1.1_Listener' | '1.2_Librarian' | '1.3_Gatekeeper' | '1.4_Reader' | '1.5_Advisor' | '1.6_Registrar';
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
  recommendations?: TrackRecommendation[];
  actionPrompt?: 'AUTH_REQUIRED' | 'UPLOAD_RELEVE' | 'CONFIRM_SHORTLIST' | 'BOOK_SLOT';
  citations?: {
    id: string;
    documentTitle: string;
    section?: string;
    snippet?: string;
  }[];
}

class ChatService {
  private memoryHistory: ChatMessage[] = [...INITIAL_MESSAGES];

  public getLocalHistory(): ChatMessage[] {
    return this.memoryHistory;
  }

  public async sendMessage(payload: SendMessagePayload): Promise<ChatMessage> {
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
      // Attempt backend call
      const res = await apiClient.post<ChatResponse>('/chat/message', payload);
      const botMsg: ChatMessage = {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: res.subAgent,
        content: res.reply,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        detectedDialect: res.detectedDialect,
        recommendations: res.recommendations,
        citations: res.citations,
        actionPrompt: res.actionPrompt,
      };
      this.memoryHistory.push(botMsg);
      return botMsg;
    } catch {
      // Graceful fallback for offline frontend dev / testing
      const botMsg = this.generateFallbackResponse(payload.message);
      this.memoryHistory.push(botMsg);
      return botMsg;
    }
  }

  public generateFallbackResponse(userText: string): ChatMessage {
    const lower = userText.toLowerCase();
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Arabizi or Derja detection simulation (VISION 1.1 Listener)
    const isArabizi = /[0-9]/.test(userText) && /(3aslema|chnowa|nheb|kifech|fama)/i.test(userText);
    const isDerja = /(شنوة|نحب|كيفاش|برنامج)/.test(userText);

    if (lower.includes('informatique') || lower.includes('software') || lower.includes('code') || lower.includes('info') || isArabizi) {
      return {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: '1.5_Advisor',
        detectedDialect: isArabizi ? 'Arabizi' : isDerja ? 'Tunisian_Derja' : 'French',
        content: isArabizi
          ? "Mar7ba bik! Hasb l'profil mte3ek w li qolthouli, fama zouz parcours ywalmouk barcha fi l'IIT:"
          : "Excellente orientation ! Selon vos intérêts en ingénierie et nouvelles technologies, voici les 2 filières les plus adaptées au sein du campus NAU :",
        timestamp: timeStr,
        citations: [
          {
            id: 'c1',
            documentTitle: 'Guide des Études IIT 2026/2027',
            section: 'Génie Informatique & Systèmes Distribués',
            snippet: 'Cursus accrédité préparant aux métiers d’architecte logiciel, ingénieur IA et Cloud.',
          },
        ],
        recommendations: [
          {
            trackCode: 'IIT-INFO-ING',
            trackName: 'Cycle Ingénieur en Génie Informatique',
            school: 'IIT',
            compatibilityScore: 94,
            matchingStrengths: ['Algorithmique', 'Intelligence Artificielle', 'Cloud Computing'],
            justification: 'Forte adéquation avec votre profil technique et vos ambitions en génie logiciel.',
          },
          {
            trackCode: 'ISB-BA-LIC',
            trackName: 'Licence Business Administration & Digital',
            school: 'ISB',
            compatibilityScore: 78,
            matchingStrengths: ['Management Technologique', 'Business Analytics'],
            justification: 'Option intéressante pour coupler compétences managériales et analytiques.',
          },
        ],
        actionPrompt: 'CONFIRM_SHORTLIST',
      };
    }

    if (lower.includes('releve') || lower.includes('note') || lower.includes('inscription')) {
      return {
        id: `vis-${Date.now()}`,
        sender: 'vision',
        subAgent: '1.4_Reader',
        content: "Pour valider officiellement votre pré-inscription et passer le court QCM d'admission, nous vous invitons à vous authentifier (Email OTP ou Google). Vos échanges anonymes actuels seront automatiquement conservés.",
        timestamp: timeStr,
        actionPrompt: 'AUTH_REQUIRED',
      };
    }

    // Default Librarian RAG answer
    return {
      id: `vis-${Date.now()}`,
      sender: 'vision',
      subAgent: '1.2_Librarian',
      content: "J'ai bien noté votre demande. À l'IIT (Institut International de Technologie) et à l'ISB (International School of Business), les admissions se font sur dossier académique suivi d'un entretien avec le Responsable pédagogique. Vous pouvez me poser des questions sur les filières, les équivalences ou les débouchés !",
      timestamp: timeStr,
      citations: [
        {
          id: 'c0',
          documentTitle: 'Règlement des admissions NAU 2026/2027',
          section: 'Conditions d’éligibilité et passage des entretiens',
        },
      ],
    };
  }
}

export const chatService = new ChatService();

