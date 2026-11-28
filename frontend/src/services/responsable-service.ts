import { apiClient } from '@/lib/api-client';
import { isApiError } from '@/lib/api-error';
import { authService } from '@/services/auth-service';
import {
  CandidateDossier,
  CatalogProgramItem,
  HumanDecision,
  KnowledgeDocumentItem,
  SchoolCode,
} from '@/types';
import {
  MOCK_CATALOG_PROGRAMS,
  MOCK_DOSSIERS,
  MOCK_KNOWLEDGE_DOCUMENTS,
} from '@/lib/mock-data';

export interface SubmitDecisionPayload {
  dossierId: string;
  decision: HumanDecision;
  notes: string;
  responsableName: string;
}

export interface UpdateCatalogProgramPayload {
  title?: string;
  description?: string;
  duration_years?: number;
  source_document?: string;
  is_active?: boolean;
}

export interface UploadKnowledgeDocumentPayload {
  code: string;
  title: string;
  school_code: 'iit' | 'isb' | 'isa' | 'nau_common';
  doc_type: string;
  description?: string;
  is_published: boolean;
  file?: File | null;
  content?: string;
}

class ResponsableService {
  private dossiers: CandidateDossier[] = [...MOCK_DOSSIERS];
  private localPrograms: CatalogProgramItem[] = [...MOCK_CATALOG_PROGRAMS];
  private localDocuments: KnowledgeDocumentItem[] = [...MOCK_KNOWLEDGE_DOCUMENTS];

  public async getDossiers(school?: SchoolCode): Promise<CandidateDossier[]> {
    try {
      const endpoint = school ? `/responsable/dossiers?school=${school}` : '/responsable/dossiers';
      return await apiClient.get<CandidateDossier[]>(endpoint);
    } catch {
      if (school) {
        return this.dossiers.filter((d) => d.targetSchool === school);
      }
      return [...this.dossiers];
    }
  }

  public async getDossierById(id: string): Promise<CandidateDossier | undefined> {
    try {
      return await apiClient.get<CandidateDossier>(`/responsable/dossiers/${id}`);
    } catch {
      return this.dossiers.find((d) => d.id === id);
    }
  }

  public async submitHumanDecision(payload: SubmitDecisionPayload): Promise<CandidateDossier> {
    try {
      return await apiClient.post<CandidateDossier>(
        `/responsable/dossiers/${payload.dossierId}/decision`,
        payload,
      );
    } catch {
      const target = this.dossiers.find((d) => d.id === payload.dossierId);
      if (!target) {
        throw new Error('Dossier introuvable');
      }

      target.status = payload.decision;
      target.humanDecision = {
        decidedBy: payload.responsableName,
        decision: payload.decision,
        notes: payload.notes,
        decidedAt: new Date().toISOString(),
      };

      return { ...target };
    }
  }

  /**
   * Live catalog lookup (`GET /api/v1/catalog/programs`) for Responsable DB supervision.
   */
  public async getCatalogPrograms(school?: SchoolCode | 'ALL'): Promise<CatalogProgramItem[]> {
    try {
      const query =
        school && school !== 'ALL'
          ? `?school_code=${school.toLowerCase()}&is_active=true&limit=100`
          : '?is_active=true&limit=100';
      const res = await apiClient.get<{ total: number; items: CatalogProgramItem[] }>(
        `/catalog/programs${query}`,
      );
      if (Array.isArray(res.items)) {
        this.localPrograms = res.items;
        return res.items;
      }
    } catch {
      // Fallback to local snapshot when backend DB is unreachable
    }
    if (school && school !== 'ALL') {
      return this.localPrograms.filter((p) => p.school_code === school.toLowerCase());
    }
    return [...this.localPrograms];
  }

  private isUsingDemoOfflineToken(): boolean {
    const sessionToken = authService.getSession().token;
    if (sessionToken && sessionToken.startsWith('demo-')) {
      return true;
    }
    if (typeof window !== 'undefined') {
      try {
        const stored = localStorage.getItem('smartcampus_token');
        return Boolean(stored && stored.startsWith('demo-'));
      } catch {
        return false;
      }
    }
    return false;
  }

  private canFallbackToLocalPreview(err: unknown): boolean {
    if (isApiError(err) && err.status === 0) {
      return true;
    }
    if (this.isUsingDemoOfflineToken() && (!isApiError(err) || err.status === 401)) {
      return true;
    }
    return false;
  }

  /**
   * School-scoped program update (`PATCH /api/v1/catalog/programs/{program_id}`).
   */
  public async updateCatalogProgram(
    programId: string,
    payload: UpdateCatalogProgramPayload,
  ): Promise<{ item: CatalogProgramItem; syncedBackend: boolean }> {
    try {
      const updated = await apiClient.patch<CatalogProgramItem>(
        `/catalog/programs/${programId}`,
        payload,
      );
      this.localPrograms = this.localPrograms.map((p) => (p.id === programId ? updated : p));
      return { item: updated, syncedBackend: true };
    } catch (err: unknown) {
      if (!this.canFallbackToLocalPreview(err)) {
        throw err;
      }
      const existing = this.localPrograms.find((p) => p.id === programId);
      if (!existing) {
        throw new Error('Formation introuvable dans le catalogue.');
      }
      const patched: CatalogProgramItem = {
        ...existing,
        ...payload,
      };
      this.localPrograms = this.localPrograms.map((p) => (p.id === programId ? patched : p));
      return { item: patched, syncedBackend: false };
    }
  }

  public async updateProgram(
    programId: string,
    payload: UpdateCatalogProgramPayload,
  ): Promise<{ item: CatalogProgramItem; syncedBackend: boolean }> {
    return this.updateCatalogProgram(programId, payload);
  }

  /**
   * Live RAG corpus list (`GET /api/v1/knowledge/documents`) for Responsable DB supervision.
   */
  public async getKnowledgeDocuments(
    school?: SchoolCode | 'ALL',
  ): Promise<KnowledgeDocumentItem[]> {
    try {
      const query =
        school && school !== 'ALL'
          ? `?school_code=${school.toLowerCase()}&page=1&page_size=100`
          : '?page=1&page_size=100';
      const res = await apiClient.get<{ total: number; items: KnowledgeDocumentItem[] }>(
        `/knowledge/documents${query}`,
      );
      if (Array.isArray(res.items)) {
        this.localDocuments = res.items;
        return res.items;
      }
    } catch {
      // Fallback to local snapshot when backend DB is unreachable
    }
    if (school && school !== 'ALL') {
      return this.localDocuments.filter((d) => d.school_code === school.toLowerCase());
    }
    return [...this.localDocuments];
  }

  /**
   * Uploads or registers a new official RAG document (`POST /api/v1/knowledge/documents`).
   */
  public async uploadKnowledgeDocument(
    payload: UploadKnowledgeDocumentPayload,
  ): Promise<{ item: KnowledgeDocumentItem; syncedBackend: boolean }> {
    const formData = new FormData();
    formData.append('code', payload.code.trim());
    formData.append('title', payload.title.trim());
    formData.append('school_code', payload.school_code);
    formData.append('doc_type', payload.doc_type);
    formData.append('is_published', String(payload.is_published));
    if (payload.description && payload.description.trim()) {
      formData.append('description', payload.description.trim());
    }
    if (payload.file) {
      formData.append('file', payload.file);
    } else if (payload.content && payload.content.trim()) {
      formData.append('content', payload.content.trim());
    }

    try {
      const created = await apiClient.upload<KnowledgeDocumentItem>(
        '/knowledge/documents',
        formData,
      );
      this.localDocuments = [created, ...this.localDocuments.filter((d) => d.code !== created.code)];
      return { item: created, syncedBackend: true };
    } catch (err: unknown) {
      if (!this.canFallbackToLocalPreview(err)) {
        throw err;
      }
      const fallbackDoc: KnowledgeDocumentItem = {
        id: `doc-local-${Date.now()}`,
        code: payload.code.trim(),
        title: payload.title.trim(),
        description: payload.description?.trim() || null,
        school_code: payload.school_code,
        doc_type: payload.doc_type,
        status: payload.is_published ? 'PUBLISHED' : 'DRAFT',
        is_published: payload.is_published,
        index_status: 'PENDING',
        latest_version_number: 1,
        updated_at: new Date().toISOString(),
      };
      this.localDocuments = [
        fallbackDoc,
        ...this.localDocuments.filter((d) => d.code !== fallbackDoc.code),
      ];
      return { item: fallbackDoc, syncedBackend: false };
    }
  }
}

export const responsableService = new ResponsableService();
