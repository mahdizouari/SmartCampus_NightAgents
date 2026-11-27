import { apiClient } from '@/lib/api-client';
import {
  CampusScope,
  ChatMessage,
  VoiceNoteMode,
  VoiceNoteProcessRequest,
  VoiceNoteProcessResponse,
  VoiceNoteTranscribeResponse,
} from '@/types';
import { chatService } from '@/services/chat-service';

class VoiceNoteService {
  /**
   * Step 1: Transcribe recorded audio with Whisper STT.
   * Does NOT invoke LLM; returns editable transcript for Visiteur/Candidat review.
   */
  public async transcribe(
    audioBlob: Blob,
    mode: VoiceNoteMode = 'auto',
    campusScope: CampusScope = 'all',
  ): Promise<VoiceNoteTranscribeResponse> {
    const formData = new FormData();
    const mime = audioBlob.type || 'audio/webm';
    let extension = 'webm';
    if (mime.includes('mp4')) extension = 'mp4';
    else if (mime.includes('ogg')) extension = 'ogg';
    else if (mime.includes('wav')) extension = 'wav';
    else if (mime.includes('mp3') || mime.includes('mpeg')) extension = 'mp3';

    formData.append('audio', audioBlob, `voice-note.${extension}`);
    formData.append('mode', mode);
    formData.append('campus_scope', campusScope);

    try {
      return await apiClient.upload<VoiceNoteTranscribeResponse>(
        '/voice-notes/transcribe',
        formData,
      );
    } catch {
      const fallbackTranscripts: Record<VoiceNoteMode, string> = {
        derja:
          'عسلامة، نحب نسجل في مرحلة تكوين المهندسين في IIT وفما برشا اختصاصات كيفاش نختار وبقداش التسجيل؟',
        ar: 'مرحبا، أود الاستفسار عن شروط القبول والتسجيل في ماجستير إدارة الأعمال في ISB ومشاريع التخرج PFE.',
        fr: "Bonjour, je souhaite me renseigner sur les filières d'ingénierie à l'IIT et les modalités d'admission.",
        auto: "3aslema! Nheb na3ref kifech nsajel fi l'IIT w l'ISB w chnowa les filières d'ingénierie w architecture disponibles.",
      };
      return {
        transcript_id: `vn-dev-${Date.now()}`,
        transcript: fallbackTranscripts[mode] || fallbackTranscripts.auto,
        mode,
        campus_scope: campusScope,
        requires_review: true,
      };
    }
  }

  /**
   * Step 2: Route confirmed transcript to VISION supervisor pipeline.
   */
  public async process(payload: VoiceNoteProcessRequest): Promise<ChatMessage> {
    const activeGuestSid = payload.session_id || apiClient.getStoredGuestSessionId() || undefined;
    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      content: payload.final_transcript,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      isVoice: true,
    };
    chatService.appendMessage(userMsg);

    try {
      const res = await apiClient.post<VoiceNoteProcessResponse>('/voice-notes/process', {
        ...payload,
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
        citations: res.citations,
        actionPrompt: res.actionPrompt,
        actorStage: res.actor_stage,
        candidateStatus: res.candidate_status,
      };
      chatService.appendMessage(botMsg);
      return botMsg;
    } catch {
      const botMsg = chatService.generateFallbackResponse(payload.final_transcript);
      chatService.appendMessage(botMsg);
      return botMsg;
    }
  }
}

export const voiceNoteService = new VoiceNoteService();
