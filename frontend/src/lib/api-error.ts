/**
 * SmartCampus AI — Standardized API Error Handling
 */

export interface ValidationErrorDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly details?: ValidationErrorDetail[] | Record<string, unknown>;

  constructor(message: string, status: number = 500, code: string = 'API_ERROR', details?: ValidationErrorDetail[] | Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;

    if (Error.captureStackTrace) {
      Error.captureStackTrace(this, ApiError);
    }
  }

  static fromResponse(status: number, data: unknown): ApiError {
    let message = 'Une erreur inattendue est survenue.';
    let code = `HTTP_${status}`;
    let details: ValidationErrorDetail[] | undefined = undefined;

    let hasExplicitMessage = false;

    if (data && typeof data === 'object') {
      const errObj = data as Record<string, unknown>;
      if (typeof errObj.detail === 'string' && errObj.detail.trim()) {
        message = errObj.detail;
        hasExplicitMessage = true;
      } else if (Array.isArray(errObj.detail)) {
        // FastAPI / Pydantic validation errors array
        message = 'Erreur de validation des données transmises.';
        code = 'VALIDATION_ERROR';
        details = errObj.detail as ValidationErrorDetail[];
        hasExplicitMessage = true;
      } else if (typeof errObj.message === 'string' && errObj.message.trim()) {
        message = errObj.message;
        hasExplicitMessage = true;
      }

      if (typeof errObj.code === 'string') {
        code = errObj.code;
      }
    }

    if (status === 401) {
      if (!hasExplicitMessage) {
        message = 'Authentification requise pour cette action.';
      }
      code = 'UNAUTHORIZED';
    } else if (status === 403) {
      if (!hasExplicitMessage) {
        message = 'Accès non autorisé pour ce profil.';
      }
      code = 'FORBIDDEN';
    } else if (status === 404) {
      if (!hasExplicitMessage) {
        message = 'Ressource introuvable.';
      }
      code = 'NOT_FOUND';
    } else if (status === 409) {
      code = 'CONFLICT';
    } else if (status === 429) {
      code = 'RATE_LIMITED';
    } else if (status >= 500) {
      code = 'SERVER_ERROR';
    }

    return new ApiError(message, status, code, details);
  }

  static networkError(originalError?: Error): ApiError {
    return new ApiError(
      'Impossible de joindre le serveur SmartCampus. Veuillez vérifier votre connexion.',
      0,
      'NETWORK_ERROR',
      originalError ? { message: originalError.message } : undefined
    );
  }

  static timeoutError(): ApiError {
    return new ApiError(
      'La requête a expiré. Le serveur prend trop de temps à répondre.',
      408,
      'TIMEOUT_ERROR'
    );
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function formatErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.details && Array.isArray(error.details)) {
      const first = error.details[0];
      if (first?.msg) {
        return `${error.message} (${first.msg})`;
      }
    }
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Une erreur inconnue est survenue.';
}

