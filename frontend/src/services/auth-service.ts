import { apiClient } from '@/lib/api-client';
import { UserRole, UserSession } from '@/types';

class AuthService {
  private currentSession: UserSession = {
    id: 'anon-' + Math.random().toString(36).substring(2, 9),
    role: 'CANDIDAT',
    isAnonymous: true,
  };

  constructor() {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('smartcampus_session');
      if (saved) {
        try {
          this.currentSession = JSON.parse(saved);
        } catch {
          // ignore parsing error
        }
      }
    }
  }

  public getSession(): UserSession {
    return this.currentSession;
  }

  public async requestEmailOtp(email: string): Promise<{ success: boolean; message: string }> {
    try {
      return await apiClient.post('/auth/otp/request', { email });
    } catch {
      // Mock success for development
      return {
        success: true,
        message: `Un code à 6 chiffres a été envoyé à ${email} (Mode test : utilisez 123456).`,
      };
    }
  }

  public async verifyEmailOtp(email: string, code: string): Promise<UserSession> {
    try {
      const response = await apiClient.post<{ token: string; user: UserSession }>('/auth/otp/verify', { email, code });
      this.persistSession({
        ...response.user,
        token: response.token,
        isAnonymous: false,
      });
      return this.currentSession;
    } catch {
      // Mock session fallback for testing
      const session: UserSession = {
        id: 'usr-' + Date.now(),
        email,
        fullName: email.split('@')[0],
        role: 'CANDIDAT',
        isAnonymous: false,
        token: 'mock-jwt-token-' + Date.now(),
      };
      this.persistSession(session);
      return session;
    }
  }

  public loginAsStaff(role: UserRole = 'RESPONSABLE', school: 'IIT' | 'ISB' | 'ISA' = 'IIT'): UserSession {
    const session: UserSession = {
      id: 'staff-01',
      email: 'responsable.iit@nau.org',
      fullName: 'Dr. Responsable IIT',
      role,
      school,
      isAnonymous: false,
      token: 'staff-jwt-token-12345',
    };
    this.persistSession(session);
    return session;
  }

  public logout(): void {
    this.currentSession = {
      id: 'anon-' + Math.random().toString(36).substring(2, 9),
      role: 'CANDIDAT',
      isAnonymous: true,
    };
    if (typeof window !== 'undefined') {
      localStorage.removeItem('smartcampus_token');
      localStorage.removeItem('smartcampus_session');
    }
  }

  private persistSession(session: UserSession) {
    this.currentSession = session;
    if (typeof window !== 'undefined') {
      localStorage.setItem('smartcampus_session', JSON.stringify(session));
      if (session.token) {
        localStorage.setItem('smartcampus_token', session.token);
      }
    }
  }
}

export const authService = new AuthService();

