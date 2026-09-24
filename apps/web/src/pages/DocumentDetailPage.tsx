import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { useDeleteDocument, useDocument } from '@/api/hooks'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ErrorState, SkeletonList } from '@/components/states'
import { Badge, InlineError, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { documentStatusMeta } from '@/features/documents/status'
import { formatBytes, formatDateTime } from '@/lib/format'

export function DocumentDetailPage(): ReactNode {
  const { documentId } = useParams<{ documentId: string }>()
  const document = useDocument(documentId, {
    // Keep the ingestion status live while the pipeline is still working.
    refetchInterval: (query) =>
      query.state.data !== undefined && documentStatusMeta(query.state.data.status).processing
        ? 3000
        : false,
  })
  const remove = useDeleteDocument()
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  useDocumentTitle(document.data?.filename ?? 'Document')

  if (document.isPending) {
    return <SkeletonList rows={4} />
  }

  if (document.error !== null) {
    return (
      <ErrorState
        error={document.error}
        title="Could not load this document"
        onRetry={() => {
          void document.refetch()
        }}
      />
    )
  }

  const meta = documentStatusMeta(document.data.status)

  return (
    <>
      <PageHeader
        title={document.data.filename}
        description="Full ingestion state, including what the quarantine scanner found."
        actions={
          <>
            <Link className="btn" to="/documents">
              All documents
            </Link>
            <button type="button" className="btn btn-danger" onClick={() => setConfirming(true)}>
              Delete
            </button>
          </>
        }
      />

      <section className="card" aria-label="Ingestion detail">
        <p className="row">
          <Badge tone={meta.tone}>{meta.label}</Badge>
          {meta.processing ? (
            <span className="row muted" role="status">
              <span className="spinner" aria-hidden="true" /> ingesting…
            </span>
          ) : null}
        </p>
        <dl className="citation-facts">
          <dt>Document id</dt>
          <dd>{document.data.id}</dd>
          <dt>Course id</dt>
          <dd>{document.data.course_id}</dd>
          <dt>Chunks</dt>
          <dd>{document.data.chunk_count}</dd>
          <dt>Pages</dt>
          <dd>{document.data.page_count ?? '—'}</dd>
          <dt>Size</dt>
          <dd>{formatBytes(document.data.size_bytes)}</dd>
          <dt>Content type</dt>
          <dd>{document.data.content_type ?? '—'}</dd>
          <dt>Source type</dt>
          <dd>{document.data.source_type}</dd>
          <dt>SHA-256</dt>
          <dd>{document.data.sha256}</dd>
          <dt>Quarantine</dt>
          <dd>{document.data.quarantine_state}</dd>
          <dt>Injection score</dt>
          <dd>{document.data.injection_score}</dd>
          <dt>Injection classes</dt>
          <dd>
            {(document.data.injection_classes ?? []).length === 0
              ? 'none'
              : (document.data.injection_classes ?? []).join(', ')}
          </dd>
          <dt>Added</dt>
          <dd>{formatDateTime(document.data.created_at)}</dd>
        </dl>
        {document.data.error_message === null ? null : (
          <p className="banner banner-danger" role="alert">
            <span aria-hidden="true">⚠</span>
            <span>{document.data.error_message}</span>
          </p>
        )}
      </section>

      <InlineError error={remove.error} />

      <ConfirmDialog
        open={confirming}
        title="Delete this document?"
        description="The stored object and its chunks will be removed, and citations pointing at it will stop resolving."
        confirmLabel="Delete document"
        destructive
        busy={remove.isPending}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          remove.mutate(document.data.id, {
            onSuccess: () => navigate('/documents'),
          })
        }}
      />
    </>
  )
}
