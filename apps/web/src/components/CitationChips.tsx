import { useId, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { AnyCitation } from '@/api/types'
import { citationHref, toCitationViews, type CitationView } from '@/lib/citations'

function CitationPanel({ citation, id }: { citation: CitationView; id: string }): ReactNode {
  return (
    <div className="citation-panel" id={id} role="region" aria-label={`Source ${citation.filename}`}>
      {citation.quote === null ? (
        <p className="muted" style={{ marginTop: 0 }}>
          The API returns citation metadata, not the passage text. Open the source document to read
          the passage in context.
        </p>
      ) : (
        <blockquote>{citation.quote}</blockquote>
      )}
      <dl className="citation-facts">
        <dt>Filename</dt>
        <dd>{citation.filename}</dd>
        <dt>Page</dt>
        <dd>{citation.page === null ? 'not recorded' : String(citation.page)}</dd>
        <dt>Source type</dt>
        <dd>{citation.sourceTypeLabel}</dd>
        <dt>Document</dt>
        <dd>{citation.documentId}</dd>
        {citation.chunkId === null ? null : (
          <>
            <dt>Chunk</dt>
            <dd>{citation.chunkId}</dd>
          </>
        )}
        <dt>Citation</dt>
        <dd>{citation.citationId}</dd>
      </dl>
      <p style={{ margin: '0.5rem 0 0' }}>
        <Link to={citationHref(citation)}>Open source document</Link>
      </p>
    </div>
  )
}

/**
 * Citations rendered as chips, each expanding to the source it points at.
 *
 * The previous client discarded the citations the API returned; this component
 * is the reason an answer can be checked rather than trusted.
 */
export function CitationChips({
  citations,
  heading = 'Sources',
}: {
  citations: readonly AnyCitation[]
  heading?: string
}): ReactNode {
  const views = useMemo(() => toCitationViews(citations), [citations])
  const [openId, setOpenId] = useState<string | null>(null)
  const baseId = useId()

  if (views.length === 0) return null

  return (
    <div className="citation-block">
      <p className="stat-label" style={{ margin: '0.5rem 0 0' }}>
        {heading} ({views.length})
      </p>
      <ul className="chip-row" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {views.map((citation, index) => {
          const panelId = `${baseId}-citation-${index}`
          const isOpen = openId === citation.citationId
          return (
            <li key={`${citation.citationId}-${index}`}>
              <button
                type="button"
                className="citation-chip"
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => setOpenId(isOpen ? null : citation.citationId)}
              >
                <span className="citation-index" aria-hidden="true">
                  [{index + 1}]
                </span>
                <span>
                  {citation.filename}
                  {citation.page === null ? '' : `, p.${citation.page}`}
                </span>
              </button>
              {isOpen ? <CitationPanel citation={citation} id={panelId} /> : null}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
