import type { DocumentStatus } from '@/api/types'
import type { Tone } from '@/components/ui'

export interface DocumentStatusMeta {
  label: string
  tone: Tone
  /** True while the ingestion pipeline is still working on the document. */
  processing: boolean
}

export const DOCUMENT_STATUS_META: Record<DocumentStatus, DocumentStatusMeta> = {
  pending: { label: 'Pending', tone: 'neutral', processing: true },
  parsing: { label: 'Parsing', tone: 'info', processing: true },
  chunking: { label: 'Chunking', tone: 'info', processing: true },
  embedding: { label: 'Embedding', tone: 'info', processing: true },
  indexing: { label: 'Indexing', tone: 'info', processing: true },
  ready: { label: 'Ready', tone: 'success', processing: false },
  failed: { label: 'Failed', tone: 'danger', processing: false },
}

export function documentStatusMeta(status: string): DocumentStatusMeta {
  if (status in DOCUMENT_STATUS_META) {
    return DOCUMENT_STATUS_META[status as DocumentStatus]
  }
  // The API may add stages; unknown stages are shown verbatim rather than
  // mislabelled as ready.
  return { label: status, tone: 'neutral', processing: true }
}

export const SOURCE_TYPE_OPTIONS = [
  'lecture',
  'slide',
  'paper',
  'book',
  'syllabus',
  'notes',
  'documentation',
  'other',
] as const
