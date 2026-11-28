import { apiClient } from '@/lib/api-client';
import { TranscriptSyncSummary } from '@/types';

class DocumentService {
  /**
   * Uploads an authenticated Candidate's official transcript (PNG, JPEG, WEBP, or PDF)
   * to VISION 1.4_Reader (`POST /api/v1/documents/extract-and-sync-transcript`),
   * returning extracted Tunisian Bac/Licence grades, confidence scores, and HITL flags.
   */
  public async extractAndSyncTranscript(
    file: File,
    options?: { declaredAverage?: number; confirmedByStudent?: boolean },
  ): Promise<TranscriptSyncSummary> {
    const formData = new FormData();
    formData.append('file', file);
    if (options?.declaredAverage !== undefined) {
      formData.append('declared_average', String(options.declaredAverage));
    }
    formData.append('confirmed_by_student', String(Boolean(options?.confirmedByStudent)));

    return await apiClient.upload<TranscriptSyncSummary>(
      '/documents/extract-and-sync-transcript',
      formData,
      { timeoutMs: 95000 },
    );
  }
}

export const documentService = new DocumentService();
