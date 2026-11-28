/**
 * SmartCampus AI — Core API Client Wrapper
 * Handles JWT injection, signed Visitor cookie credentials, X-Session-ID propagation,
 * timeout, JSON/FormData formatting, and unified ApiError handling.
 */

import { ApiError } from './api-error';

export const GUEST_SESSION_STORAGE_KEY = 'smartcampus_guest_session_id';
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isValidUuid(value: string | null | undefined): value is string {
  return Boolean(value && UUID_V4_RE.test(value.trim()));
}

export interface RequestOptions extends RequestInit {
  timeoutMs?: number;
  token?: string;
  skipAuth?: boolean;
  sessionId?: string;
}

export interface ApiResponseEnvelope<T> {
  success: boolean;
  data: T;
  message?: string;
  meta?: Record<string, unknown>;
}

class ApiClient {
  private baseUrl: string;

  constructor() {
    this.baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
  }

  public setBaseUrl(url: string) {
    this.baseUrl = url;
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  /**
   * Retrieves active auth token from browser storage if running client-side.
   */
  private getStoredToken(): string | null {
    if (typeof window === 'undefined') return null;
    try {
      return localStorage.getItem('smartcampus_token');
    } catch {
      return null;
    }
  }

  /**
   * Retrieves active Visitor guest session UUID if running client-side.
   */
  public getStoredGuestSessionId(): string | null {
    if (typeof window === 'undefined') return null;
    try {
      const sid = localStorage.getItem(GUEST_SESSION_STORAGE_KEY);
      return isValidUuid(sid) ? sid.trim() : null;
    } catch {
      return null;
    }
  }

  /**
   * Persists or clears the active Visitor guest session UUID.
   */
  public setStoredGuestSessionId(sessionId: string | null): void {
    if (typeof window === 'undefined') return;
    try {
      if (sessionId && isValidUuid(sessionId)) {
        localStorage.setItem(GUEST_SESSION_STORAGE_KEY, sessionId.trim());
      } else {
        localStorage.removeItem(GUEST_SESSION_STORAGE_KEY);
      }
    } catch {
      // ignore storage errors
    }
  }

  /**
   * Core request dispatcher with timeout, cookie credentials, and standardized error trapping.
   */
  public async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const {
      timeoutMs = 20000,
      token,
      skipAuth = false,
      sessionId,
      headers = {},
      ...customConfig
    } = options;

    const url = endpoint.startsWith('http')
      ? endpoint
      : `${this.baseUrl}${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    const activeToken = token || (!skipAuth ? this.getStoredToken() : null);
    const activeGuestSid = sessionId || this.getStoredGuestSessionId();

    const mergedHeaders: Record<string, string> = {
      Accept: 'application/json',
      ...(headers as Record<string, string>),
    };

    if (activeToken) {
      mergedHeaders['Authorization'] = `Bearer ${activeToken}`;
    }

    // Attach X-Session-ID for Visitor requests and session-merge endpoints
    if (
      activeGuestSid &&
      isValidUuid(activeGuestSid) &&
      (!activeToken || endpoint.includes('/auth/'))
    ) {
      mergedHeaders['X-Session-ID'] = activeGuestSid;
    }

    // Default to JSON body if object is passed and not already FormData
    if (
      customConfig.body &&
      !(customConfig.body instanceof FormData) &&
      !(customConfig.body instanceof Blob)
    ) {
      if (typeof customConfig.body === 'object') {
        customConfig.body = JSON.stringify(customConfig.body);
        mergedHeaders['Content-Type'] = 'application/json';
      }
    }

    try {
      const response = await fetch(url, {
        ...customConfig,
        credentials: customConfig.credentials || 'include',
        headers: mergedHeaders,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      // Capture issued/refreshed X-Session-ID from backend guest session endpoints
      const responseSid = response.headers.get('X-Session-ID');
      if (!activeToken && isValidUuid(responseSid)) {
        this.setStoredGuestSessionId(responseSid);
      }

      const contentType = response.headers.get('content-type');
      let data: unknown = null;

      if (contentType && contentType.includes('application/json')) {
        data = await response.json();
      } else {
        const text = await response.text();
        data = text ? { message: text } : {};
      }

      if (!response.ok) {
        throw ApiError.fromResponse(response.status, data);
      }

      return data as T;
    } catch (err: unknown) {
      clearTimeout(timeoutId);

      if (err instanceof ApiError) {
        throw err;
      }

      if (err instanceof Error) {
        if (err.name === 'AbortError') {
          throw ApiError.timeoutError();
        }
        throw ApiError.networkError(err);
      }

      throw new ApiError('Une erreur inattendue est survenue.', 500, 'UNKNOWN_ERROR');
    }
  }

  public get<T>(endpoint: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'GET' });
  }

  public post<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'POST', body: body as BodyInit });
  }

  public put<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'PUT', body: body as BodyInit });
  }

  public patch<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'PATCH', body: body as BodyInit });
  }

  public delete<T>(endpoint: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'DELETE' });
  }

  /**
   * Uploads file payload (e.g. official transcript or RAG corpus brochure) using multipart/form-data.
   */
  public async upload<T>(
    endpoint: string,
    formData: FormData,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: formData,
      headers: {
        ...(options?.headers || {}),
      },
    });
  }
}

export const apiClient = new ApiClient();
