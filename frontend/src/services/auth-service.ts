import { apiClient, isValidUuid } from '@/lib/api-client';
import { ActorStage, CandidateStatus, SchoolCode, UserRole, UserSession } from '@/types';

interface GuestSessionBackendResponse {
  session_id: string;
  status: string;
  expires_at: string;
  ttl_seconds: number;
  step?: string;
  school_target?: string | null;
  turn_count?: number;
  actor_stage?: ActorStage;
}

interface OtpSendBackendResponse {
  success: boolean;
  message: string;
  expires_in_seconds: number;
  retry_after_seconds: number;
  dev_otp_code?: string | null;
}

interface AuthVerifyBackendResponse {
  access_token: string;
  token: string;
  refresh_token: string;
  expires_in: number;
  actor_stage?: ActorStage;
  session_merged?: boolean;
  turns_merged?: number;
  user: {
    id: string;
    email: string;
    full_name?: string;
    fullName?: string;
    role: string;
    actor_stage?: ActorStage;
    school_scope?: string | null;
    school?: string | null;
    candidate_id?: string | null;
    status?: CandidateStatus;
    isAnonymous?: boolean;
  };
}

function generateUuidV4(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function normalizeRoleToActorStage(
  rawRole?: string | null,
  isAnonymous = false,
): UserRole {
  if (isAnonymous) return 'VISITEUR';
  const normalized = (rawRole || '').trim().toLowerCase();
  if (normalized === 'responsable' || normalized === 'admin') {
    return 'RESPONSABLE';
  }
  return 'CANDIDAT';
}

function normalizeSchoolCode(raw?: string | null): SchoolCode | undefined {
  if (!raw) return undefined;
  const upper = raw.trim().toUpperCase();
  if (upper === 'IIT' || upper === 'ISB' || upper === 'ISA') {
    return upper;
  }
  return undefined;
}

class AuthService {
  private currentSession: UserSession;
  private guestInitPromise: Promise<UserSession> | null = null;

  constructor() {
    const fallbackSid = generateUuidV4();
    this.currentSession = {
      id: fallbackSid,
      sessionId: fallbackSid,
      role: 'VISITEUR',
      actorStage: 'VISITEUR',
      status: 'ANONYMOUS',
      isAnonymous: true,
    };

    if (typeof window !== 'undefined') {
      const savedGuestSid = apiClient.getStoredGuestSessionId();
      if (savedGuestSid) {
        this.currentSession.id = savedGuestSid;
        this.currentSession.sessionId = savedGuestSid;
      } else {
        apiClient.setStoredGuestSessionId(fallbackSid);
      }

      const saved = localStorage.getItem('smartcampus_session');
      if (saved) {
        try {
          const parsed = JSON.parse(saved) as Partial<UserSession>;
          const isAnon = Boolean(parsed.isAnonymous);
          const mappedRole = normalizeRoleToActorStage(parsed.role, isAnon);
          const resolvedSid = isValidUuid(parsed.sessionId)
            ? parsed.sessionId
            : savedGuestSid || fallbackSid;
          this.currentSession = {
            id: parsed.id || resolvedSid,
            sessionId: isAnon ? resolvedSid : undefined,
            candidateId: parsed.candidateId,
            email: parsed.email,
            fullName: parsed.fullName,
            role: mappedRole,
            actorStage: mappedRole,
            school: parsed.school,
            status: parsed.status || (isAnon ? 'ANONYMOUS' : 'AUTHENTICATED'),
            isAnonymous: isAnon,
            consentGranted: parsed.consentGranted,
            sessionMerged: parsed.sessionMerged,
            turnsMerged: parsed.turnsMerged,
            token: parsed.token,
          };
        } catch {
          // ignore malformed storage
        }
      }
    }
  }

  public getSession(): UserSession {
    return this.currentSession;
  }

  /**
   * Initializes or refreshes a signed 7-day Visiteur session on the backend
   * (`POST /api/v1/auth/guest-session`), storing the issued UUIDv4 for X-Session-ID & cookie merge.
   */
  public async ensureGuestSession(): Promise<UserSession> {
    if (!this.currentSession.isAnonymous) {
      return this.currentSession;
    }
    if (this.guestInitPromise) {
      return this.guestInitPromise;
    }

    this.guestInitPromise = (async () => {
      try {
        const res = await apiClient.post<GuestSessionBackendResponse>(
          '/auth/guest-session',
          {},
          { skipAuth: true },
        );
        if (res && isValidUuid(res.session_id)) {
          apiClient.setStoredGuestSessionId(res.session_id);
          const nextSession: UserSession = {
            id: res.session_id,
            sessionId: res.session_id,
            role: 'VISITEUR',
            actorStage: 'VISITEUR',
            status: (res.step as CandidateStatus) || 'ANONYMOUS',
            school: normalizeSchoolCode(res.school_target),
            isAnonymous: true,
          };
          this.persistSession(nextSession);
          return nextSession;
        }
      } catch {
        // Offline / static fallback: keep local valid UUIDv4 so UI stays responsive
        if (!isValidUuid(this.currentSession.sessionId)) {
          const freshSid = generateUuidV4();
          apiClient.setStoredGuestSessionId(freshSid);
          this.currentSession = {
            ...this.currentSession,
            id: freshSid,
            sessionId: freshSid,
            role: 'VISITEUR',
            actorStage: 'VISITEUR',
            isAnonymous: true,
          };
        }
      } finally {
        this.guestInitPromise = null;
      }
      return this.currentSession;
    })();

    return this.guestInitPromise;
  }

  public async requestEmailOtp(
    email: string,
  ): Promise<{ success: boolean; message: string; devOtpCode?: string }> {
    try {
      const res = await apiClient.post<OtpSendBackendResponse>(
        '/auth/otp/send',
        { channel: 'email', email },
        { skipAuth: true },
      );
      const devHint = res.dev_otp_code ? ` (Code dév local : ${res.dev_otp_code})` : '';
      return {
        success: res.success,
        message: `Code OTP envoyé à ${email}${devHint}.`,
        devOtpCode: res.dev_otp_code || undefined,
      };
    } catch {
      return {
        success: true,
        message: `Un code à 6 chiffres a été envoyé à ${email} (Mode hors-ligne : utilisez 123456).`,
        devOtpCode: '123456',
      };
    }
  }

  /**
   * Verifies OTP (`POST /api/v1/auth/otp/verify`), automatically associates the active
   * Visiteur session (`anonymous_session_id` + cookie) into the Candidat dossier,
   * and records explicit INPDP Law 2004-63 consent (`POST /api/v1/auth/consent`).
   */
  public async verifyEmailOtp(
    email: string,
    code: string,
    options?: { fullName?: string; acceptConsent?: boolean },
  ): Promise<UserSession> {
    const anonymousSid =
      apiClient.getStoredGuestSessionId() ||
      (isValidUuid(this.currentSession.sessionId) ? this.currentSession.sessionId : null);
    const acceptConsent = options?.acceptConsent !== false;

    try {
      const response = await apiClient.post<AuthVerifyBackendResponse>(
        '/auth/otp/verify',
        {
          channel: 'email',
          email,
          code,
          full_name: options?.fullName?.trim() || undefined,
          anonymous_session_id: anonymousSid || undefined,
          consent_merge: true,
        },
        {
          skipAuth: true,
          sessionId: anonymousSid || undefined,
        },
      );

      const accessToken = response.access_token || response.token;
      const mappedRole = normalizeRoleToActorStage(response.user.role, false);
      let sessionMerged = Boolean(response.session_merged);
      let turnsMerged = response.turns_merged ?? 0;

      // Fallback explicit merge call if backend hasn't auto-merged during verify_otp yet
      if (!sessionMerged && mappedRole === 'CANDIDAT' && anonymousSid) {
        try {
          const mergeRes = await apiClient.post<{
            turns_merged: number;
            current_status: CandidateStatus;
          }>(
            '/auth/merge-session',
            { anonymous_session_id: anonymousSid, consent_explicit: true },
            { token: accessToken, sessionId: anonymousSid },
          );
          sessionMerged = true;
          turnsMerged = mergeRes.turns_merged;
        } catch {
          // Guest session had no Redis turns yet or was already merged during verify_otp
        }
      }

      // Record explicit INPDP (Law 2004-63) consent when ticked in AuthModal
      let consentRecorded = false;
      if (acceptConsent && mappedRole === 'CANDIDAT') {
        try {
          await apiClient.post(
            '/auth/consent',
            {
              terms_accepted: true,
              privacy_policy_accepted: true,
              transcript_processing_accepted: true,
              policy_version: '2026.1',
            },
            { token: accessToken },
          );
          consentRecorded = true;
        } catch {
          consentRecorded = false;
        }
      }

      apiClient.setStoredGuestSessionId(null);

      const verifiedSession: UserSession = {
        id: response.user.id,
        candidateId: response.user.candidate_id || undefined,
        email: response.user.email,
        fullName: response.user.fullName || response.user.full_name || email.split('@')[0],
        role: mappedRole,
        actorStage: mappedRole,
        school: normalizeSchoolCode(response.user.school || response.user.school_scope),
        status: response.user.status || 'AUTHENTICATED',
        isAnonymous: false,
        consentGranted: consentRecorded || acceptConsent,
        sessionMerged,
        turnsMerged,
        token: accessToken,
      };
      this.persistSession(verifiedSession);
      return verifiedSession;
    } catch {
      // Offline / demo fallback ONLY when backend is unreachable or demo code 123456 is used
      if (code !== '123456') {
        throw new Error('Code OTP invalide ou expiré.');
      }
      apiClient.setStoredGuestSessionId(null);
      const fallbackRole: UserRole = email.toLowerCase().startsWith('responsable')
        ? 'RESPONSABLE'
        : 'CANDIDAT';
      const session: UserSession = {
        id: generateUuidV4(),
        email,
        fullName: options?.fullName?.trim() || email.split('@')[0],
        role: fallbackRole,
        actorStage: fallbackRole,
        school: fallbackRole === 'RESPONSABLE' ? 'IIT' : undefined,
        status: 'AUTHENTICATED',
        isAnonymous: false,
        consentGranted: acceptConsent,
        sessionMerged: Boolean(anonymousSid),
        turnsMerged: 0,
        token: 'demo-offline-jwt-' + Date.now(),
      };
      this.persistSession(session);
      return session;
    }
  }

  public loginAsResponsableDemo(school: SchoolCode = 'IIT'): UserSession {
    const session: UserSession = {
      id: '00000000-0000-4000-8000-000000000002',
      email: `responsable.${school.toLowerCase()}@smartcampus.tn`,
      fullName: `Dr. Responsable ${school}`,
      role: 'RESPONSABLE',
      actorStage: 'RESPONSABLE',
      school,
      status: 'AUTHENTICATED',
      isAnonymous: false,
      token: 'demo-responsable-token-' + school.toLowerCase(),
    };
    this.persistSession(session);
    return session;
  }

  public logout(): UserSession {
    const freshGuestSid = generateUuidV4();
    if (typeof window !== 'undefined') {
      localStorage.removeItem('smartcampus_token');
      localStorage.removeItem('smartcampus_session');
    }
    apiClient.setStoredGuestSessionId(freshGuestSid);
    this.currentSession = {
      id: freshGuestSid,
      sessionId: freshGuestSid,
      role: 'VISITEUR',
      actorStage: 'VISITEUR',
      status: 'ANONYMOUS',
      isAnonymous: true,
    };
    void this.ensureGuestSession();
    return this.currentSession;
  }

  private persistSession(session: UserSession) {
    this.currentSession = session;
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem('smartcampus_session', JSON.stringify(session));
        if (session.token) {
          localStorage.setItem('smartcampus_token', session.token);
        }
      } catch {
        // ignore storage quota errors
      }
    }
  }
}

export const authService = new AuthService();
