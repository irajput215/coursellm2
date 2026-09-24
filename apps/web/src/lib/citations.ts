import type { AnyCitation } from '@/api/types'
import { humaniseToken } from './format'

/**
 * A citation as the UI needs it.
 *
 * The API exposes the identifiers a reader needs to open the source (filename,
 * page, document id, source type) and — by design, see `api/schemas/chat.py` —
 * does *not* expose the retrieved passage text. If a deployment ever adds a
 * `quote`/`excerpt`/`passage` field this normaliser picks it up automatically;
 * otherwise the UI says so explicitly rather than pretending a passage exists.
 */
export interface CitationView {
  citationId: string
  filename: string
  page: number | null
  sourceType: string
  sourceTypeLabel: string
  documentId: string
  chunkId: string | null
  quote: string | null
}

function readPassage(citation: AnyCitation): string | null {
  const candidate = citation as { quote?: unknown; excerpt?: unknown; passage?: unknown }
  for (const key of ['quote', 'excerpt', 'passage'] as const) {
    const value = candidate[key]
    if (typeof value === 'string' && value.trim() !== '') return value
  }
  return null
}

export function toCitationView(citation: AnyCitation): CitationView {
  const chunkId = 'chunk_id' in citation ? citation.chunk_id : null
  return {
    citationId: citation.citation_id,
    filename: citation.filename,
    page: citation.page ?? null,
    sourceType: citation.source_type,
    sourceTypeLabel: humaniseToken(citation.source_type),
    documentId: citation.document_id,
    chunkId,
    quote: readPassage(citation),
  }
}

export function toCitationViews(citations: readonly AnyCitation[]): CitationView[] {
  return citations.map(toCitationView)
}

/** Where the reader goes to check the claim: the document it came from. */
export function citationHref(citation: CitationView): string {
  return `/documents/${citation.documentId}`
}
