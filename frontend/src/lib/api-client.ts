/**
 * SmartCampus AI — Core API Client Wrapper
 * Handles JWT injection, timeout, JSON/FormData formatting, and unified ApiError handling.
 */

import { ApiError } from './api-error';

export interface RequestOptions extends RequestInit {
  timeoutMs?: number;
  token?: string;
  skipAuth?: boolean;
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
   * Core request dispatcher with timeout and standardized error trapping.
   */
  public async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { timeoutMs = 15000, token, skipAuth = false, headers = {}, ...customConfig } = options;

    const url = endpoint.startsWith('http') ? endpoint : `${this.baseUrl}${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    const activeToken = token || (!skipAuth ? this.getStoredToken() : null);

    const mergedHeaders: HeadersInit = {
      'Accept': 'application/json',
      ...headers,
    };

    if (activeToken) {
      (mergedHeaders as Record<string, string>)['Authorization'] = `Bearer ${activeToken}`;
    }

    // Default to JSON body if object is passed and not already FormData
    if (customConfig.body && !(customConfig.body instanceof FormData) && !(customConfig.body instanceof Blob)) {
      if (typeof customConfig.body === 'object') {
        customConfig.body = JSON.stringify(customConfig.body);
        (mergedHeaders as Record<string, string>)['Content-Type'] = 'application/json';
      }
    }

    try {
      const response = await fetch(url, {
        ...customConfig,
        headers: mergedHeaders,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

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
   * Uploads file payload (e.g. relevé de notes) using multipart/form-data.
   */
  public async upload<T>(endpoint: string, formData: FormData, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: formData,
      headers: {
        // Fetch will automatically set correct boundary when multipart/form-data header is omitted
        ...(options?.headers || {}),
      },
    });
  }
}

export const apiClient = new ApiClient();

