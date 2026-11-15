/**
 * SmartCampus AI — Core Type Definitions
 * Exact alignment with Architecture v3 and Data Model specification.
 */

export type SchoolCode = 'IIT' | 'ISB' | 'ISA';

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
 * User / Auth roles
 */
export type UserRole = 'CANDIDAT' | 'RESPONSABLE' | 'HUMAN_RH' | 'DIRECTION' | 'ADMIN';

export interface UserSession {
  id: string;
  email?: string;
  fullName?: string;
  role: UserRole;
  school?: SchoolCode; // relevant for Responsables
  isAnonymous: boolean;
  token?: string;
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

export interface ChatMessage {
  id: string;
  sender: 'user' | 'vision' | 'system';
  subAgent?: '1.1_Listener' | '1.2_Librarian' | '1.3_Gatekeeper' | '1.4_Reader' | '1.5_Advisor' | '1.6_Registrar';
  content: string;
  timestamp: string;
  isVoice?: boolean;
  audioDurationSeconds?: number;
  detectedDialect?: 'French' | 'Tunisian_Derja' | 'Arabizi';
  citations?: SourceCitation[];
  recommendations?: TrackRecommendation[];
  actionPrompt?: 'AUTH_REQUIRED' | 'UPLOAD_RELEVE' | 'CONFIRM_SHORTLIST' | 'BOOK_SLOT';
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
 * Academic Program Catalog Entry
 */
export interface AcademicProgram {
  code: string;
  title: string;
  school: SchoolCode;
  diplomaLevel: 'Licence' | 'Master' | 'Ingénieur';
  description: string;
  officialSource: string;
}

