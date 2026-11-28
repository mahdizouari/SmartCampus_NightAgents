/**
 * SmartCampus AI — Core Type Definitions
 * Aligned with Visitor -> Candidate + Responsable actor model and Architecture v3.
 */

export type SchoolCode = 'IIT' | 'ISB' | 'ISA';
export type SchoolCodeLower = 'iit' | 'isb' | 'isa';

/**
 * Official Candidate Status Lifecycle (v3 §3 & data_model.md)
 */
export type CandidateStatus =
  | 'ANONYMOUS'
  | 'PROFILING'
  | 'ELIGIBLE'
  | 'INELIGIBLE'
  | 'ORIENTED'
  | 'AUTHENTICATED'
  | 'PREINSCRIBED'
  | 'QCM_DONE'
  | 'SCHEDULED'
  | 'INTERVIEWED'
  | 'ADMITTED'
  | 'WAITLIST'
  | 'REJECTED'
  | 'DORMANT'
  | 'ESCALATED'
  | 'WITHDRAWN';

/**
 * Official Appointment State Machine (v3 §3, data_model.md & SA-3)
 * Lifecycle: AWAITING_SLOT → INVITED → BOOKED → REMINDED → ATTENDED
 * Exceptions: CANCELLED, NO_SHOW, ESCALATED, WITHDRAWN
 */
export type AppointmentStatus =
  | 'AWAITING_SLOT'
  | 'INVITED'
  | 'BOOKED'
  | 'REMINDED'
  | 'ATTENDED'
  | 'CANCELLED'
  | 'NO_SHOW'
  | 'ESCALATED'
  | 'WITHDRAWN';

/**
 * Human decision choices — only the NAU Responsable can decide.
 */
export type HumanDecision = 'ADMITTED' | 'WAITLIST' | 'REJECTED';

/**
 * Simplified Post-Review Actor Model:
 * - VISITOR: Unauthenticated prospective student, graduate, or parent/relative (7-day session)
 * - CANDIDATE: Authenticated applicant after OTP/OAuth + automatic session merge
 * - RESPONSABLE: School academic supervisor, interviewer, HITL decision-maker & DB/corpus manager
 */
export type ActorStage = 'VISITOR' | 'CANDIDATE' | 'RESPONSABLE';
export type UserRole = ActorStage;

export interface UserSession {
  id: string;
  sessionId?: string;
  candidateId?: string;
  email?: string;
  fullName?: string;
  role: UserRole;
  actorStage?: ActorStage;
  school?: SchoolCode;
  status?: CandidateStatus;
  isAnonymous: boolean;
  consentGranted?: boolean;
  sessionMerged?: boolean;
  turnsMerged?: number;
  token?: string;
}

/**
 * VLM Transcript Extraction Summary (VISION 1.4_Reader — Bonus-1 & Bonus-2)
 */
export interface ExtractedGradeItem {
  id?: string;
  subject_name: string;
  grade_value: number;
  grade_scale: number;
  coefficient?: number | null;
  confidence_score?: number | null;
  needs_confirmation: boolean;
}

export interface TranscriptSyncSummary {
  document_id: string;
  document_type: 'RELEVE_BAC' | 'RELEVE_UNIVERSITAIRE';
  diploma_stage: string;
  verification_status: 'PENDING' | 'CONFIRMED_BY_STUDENT' | 'MISMATCH_RH' | 'VERIFIED';
  current_education_level?: string | null;
  current_specialty?: string | null;
  declared_overall_avg?: number | null;
  grades_persisted_count: number;
  grades: ExtractedGradeItem[];
  overall_confidence: number;
  min_confidence: number;
  discrepancy_detected: boolean;
  needs_manual_review: boolean;
  warnings: string[];
}

/**
 * Chat & Conversation Types (VISION Front-Office)
 */
export interface SourceCitation {
  id: string;
  documentTitle: string;
  section?: string;
  url?: string;
  snippet?: string;
}

export interface TrackRecommendation {
  trackCode: string;
  trackName: string;
  school: SchoolCode;
  compatibilityScore: number; // 0 to 100
  matchingStrengths: string[];
  justification: string;
}

export type VisionSubAgentId =
  | '1.1_Listener'
  | '1.2_Librarian'
  | '1.3_Gatekeeper'
  | '1.4_Reader'
  | '1.5_Advisor'
  | '1.6_Registrar';

export type ChatActionPrompt =
  | 'AUTH_REQUIRED'
  | 'UPLOAD_RELEVE'
  | 'CONFIRM_SHORTLIST'
  | 'BOOK_SLOT';

export interface ChatMessage {
  id: string;
  sender: 'user' | 'vision' | 'system';
  subAgent?: VisionSubAgentId;
  content: string;
  timestamp: string;
  isVoice?: boolean;
  audioDurationSeconds?: number;
  detected_language?: string;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
  citations?: SourceCitation[];
  recommendations?: TrackRecommendation[];
  actionPrompt?: ChatActionPrompt;
  actorStage?: ActorStage;
  candidateStatus?: CandidateStatus;
  transcriptSummary?: TranscriptSyncSummary;
}

/**
 * Candidate Dossier & Profile for Responsables
 */
export interface CandidateDossier {
  id: string;
  candidateNumber: string; // e.g. NAU-2026-0042
  fullName: string;
  email: string;
  phone: string;
  targetSchool: SchoolCode;
  targetTrack: string;
  status: CandidateStatus;
  declaredOrientationScore?: number;
  releveOcrConfidence?: number; // e.g. 0.94
  qcmScore?: number; // e.g. 8/10
  interviewSlot?: {
    date: string;
    startTime: string;
    status: AppointmentStatus;
    responsableName: string;
  };
  briefSummary?: string; // Generated 1-page synthesis by VISION 1.6
  humanDecision?: {
    decidedBy: string;
    decision: HumanDecision;
    notes: string;
    decidedAt: string;
  };
  submittedAt: string;
}

/**
 * Academic Program Catalog Entry (UI summary & live backend catalog)
 */
export interface AcademicProgram {
  code: string;
  title: string;
  school: SchoolCode;
  diplomaLevel: 'Licence' | 'Master' | 'Ingénieur';
  description: string;
  officialSource: string;
}

export type BackendDegreeLevel = 'licence' | 'prepa' | 'cycle_ingenieur' | 'master';

export interface CatalogProgramItem {
  id: string;
  code: string;
  school_code: SchoolCodeLower;
  title: string;
  degree_level: BackendDegreeLevel;
  degree_title: string;
  duration_years: number;
  study_modes: string[];
  description: string;
  source_document: string;
  is_active: boolean;
}

export interface KnowledgeDocumentItem {
  id: string;
  code: string;
  title: string;
  description?: string | null;
  school_code: string;
  doc_type: string;
  status: string;
  is_published: boolean;
  index_status: string;
  latest_version_number?: number | null;
  updated_at?: string | null;
}

/**
 * Voice Note & Whisper Transcription Types
 */
export type VoiceNoteMode = 'auto' | 'derja' | 'ar' | 'fr';
export type CampusScope = 'all' | 'IIT' | 'ISB' | 'ISA';

export interface VoiceNoteTranscribeResponse {
  transcript_id: string;
  transcript: string;
  mode: VoiceNoteMode;
  campus_scope: CampusScope;
  requires_review: boolean;
  audio_filename?: string;
  duration_seconds?: number;
}

export interface VoiceNoteProcessRequest {
  transcript_id: string;
  final_transcript: string;
  campus_scope?: CampusScope;
  mode?: VoiceNoteMode;
  session_id?: string;
}

export interface VoiceNoteProcessResponse {
  transcript_id: string;
  campus_scope: CampusScope;
  mode: VoiceNoteMode;
  final_transcript: string;
  sessionId: string;
  session_id?: string;
  reply: string;
  reply_text?: string;
  subAgent: VisionSubAgentId;
  sub_agent?: VisionSubAgentId;
  detected_language?: string;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
  recommendations?: TrackRecommendation[];
  actionPrompt?: ChatActionPrompt;
  citations?: SourceCitation[];
  actor_stage?: ActorStage | string;
  candidate_status?: CandidateStatus;
}
