import { apiClient } from '@/lib/api-client';
import { CandidateDossier, HumanDecision, SchoolCode } from '@/types';
import { MOCK_DOSSIERS } from '@/lib/mock-data';

export interface SubmitDecisionPayload {
  dossierId: string;
  decision: HumanDecision;
  notes: string;
  responsableName: string;
}

class ResponsableService {
  private dossiers: CandidateDossier[] = [...MOCK_DOSSIERS];

  public async getDossiers(school?: SchoolCode): Promise<CandidateDossier[]> {
    try {
      const endpoint = school ? `/responsable/dossiers?school=${school}` : '/responsable/dossiers';
      return await apiClient.get<CandidateDossier[]>(endpoint);
    } catch {
      // Mock fallback
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
      return await apiClient.post<CandidateDossier>(`/responsable/dossiers/${payload.dossierId}/decision`, payload);
    } catch {
      // Local state update for testing
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
}

export const responsableService = new ResponsableService();

