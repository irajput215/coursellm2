import { useId, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { useCourses, useDeleteDocument, useDocuments, useUploadDocument } from '@/api/hooks'
import type { DocumentRecord, SourceType } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, InlineError, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { SOURCE_TYPE_OPTIONS, documentStatusMeta } from '@/features/documents/status'
import { formatBytes, formatDateTime } from '@/lib/format'

export function DocumentsPage(): ReactNode {
  useDocumentTitle('Documents')
  const [searchParams] = useSearchParams()
  const courseFilter = searchParams.get('course')
  const courses = useCourses()

  const documents = useDocuments(courseFilter === null ? {} : { courseId: courseFilter }, {
    // Keep the list live only while the ingestion pipeline is still working.
    refetchInterval: (query) =>
      query.state.data?.some((doc) => documentStatusMeta(doc.status).processing) === true
        ? 3000
        : false,
  })
  const polling =
    documents.data?.some((doc) => documentStatusMeta(doc.status).processing) ?? false

  const upload = useUploadDocument()
  const remove = useDeleteDocument()
  const [file, setFile] = useState<File | null>(null)
  const [courseId, setCourseId] = useState<string>(courseFilter ?? '')
  const [newCourseName, setNewCourseName] = useState('')
  const [sourceType, setSourceType] = useState<SourceType | ''>('')
  const [reingest, setReingest] = useState(false)
  const [progress, setProgress] = useState(0)
  const [pendingDelete, setPendingDelete] = useState<DocumentRecord | null>(null)

  const fileId = useId()
  const courseSelectId = useId()
  const newCourseId = useId()
  const sourceTypeId = useId()
  const reingestId = useId()

  const selectedCourseName = useMemo(
    () => courses.data?.find((course) => course.id === courseId)?.name ?? null,
    [courseId, courses.data],
  )

  const canUpload =
    file !== null && (courseId !== '' || newCourseName.trim() !== '') && !upload.isPending

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    if (!canUpload || file === null) return
    setProgress(0)
    upload.mutate(
      {
        file,
        courseId: courseId === '' ? null : courseId,
        courseName: courseId === '' ? newCourseName.trim() : null,
        sourceType: sourceType === '' ? null : sourceType,
        reingest,
        onProgress: setProgress,
      },
      {
        onSuccess: () => {
          setFile(null)
          setProgress(0)
        },
      },
    )
  }

  return (
    <>
      <PageHeader
        title="Documents"
        description="Ingestion status is polled while a document is being processed, so progress is visible rather than guessed."
        actions={
          <Link className="btn" to="/courses">
            Courses
          </Link>
        }
      />

      <div className="card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem' }}>Upload a document</h2>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor={fileId}>File</label>
            <input
              id={fileId}
              type="file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor={courseSelectId}>Course</label>
            <select
              id={courseSelectId}
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">— new course —</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code === null ? course.name : `${course.code} — ${course.name}`}
                </option>
              ))}
            </select>
          </div>

          {courseId === '' ? (
            <div className="field">
              <label htmlFor={newCourseId}>New course name</label>
              <input
                id={newCourseId}
                type="text"
                value={newCourseName}
                onChange={(event) => setNewCourseName(event.target.value)}
                placeholder="e.g. Linear Algebra"
              />
              <span className="hint">A course is created for you if it does not exist.</span>
            </div>
          ) : null}

          <div className="field">
            <label htmlFor={sourceTypeId}>Source type</label>
            <select
              id={sourceTypeId}
              value={sourceType}
              onChange={(event) => setSourceType(event.target.value as SourceType | '')}
            >
              <option value="">Infer from the filename</option>
              {SOURCE_TYPE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <span className="row">
              <input
                id={reingestId}
                type="checkbox"
                checked={reingest}
                onChange={(event) => setReingest(event.target.checked)}
                style={{ width: 'auto' }}
              />
              <label htmlFor={reingestId}>Re-ingest if these exact bytes already exist</label>
            </span>
          </div>

          <button type="submit" className="btn btn-primary" disabled={!canUpload}>
            {upload.isPending ? 'Uploading…' : 'Upload'}
          </button>
        </form>

        {upload.isPending || progress > 0 ? (
          <div style={{ marginTop: '0.6rem' }}>
            <progress
              max={100}
              value={progress}
              aria-label="Upload progress"
              style={{ width: '100%' }}
            />
            <span className="muted" role="status">
              {progress}% uploaded
            </span>
          </div>
        ) : null}

        {upload.data === undefined ? null : (
          <p className="banner banner-info" role="status">
            <span aria-hidden="true">✓</span>
            <span>
              {upload.data.filename}: {documentStatusMeta(upload.data.status).label}
              {selectedCourseName === null ? '' : ` in ${selectedCourseName}`} ·{' '}
              {upload.data.chunks_processed} chunks processed
              {upload.data.reused ? ' (existing bytes reused)' : ''}
              {upload.data.degraded === undefined || upload.data.degraded.length === 0
                ? ''
                : ` · warnings: ${upload.data.degraded.join(', ')}`}
            </span>
          </p>
        )}
        <InlineError error={upload.error} />
      </div>

      {documents.isPending ? (
        <SkeletonList rows={4} />
      ) : documents.error !== null ? (
        <ErrorState
          error={documents.error}
          title="Could not load your documents"
          onRetry={() => {
            void documents.refetch()
          }}
        />
      ) : documents.data.length === 0 ? (
        <EmptyState
          title="No documents yet"
          description="Upload a lecture, paper or syllabus and it becomes retrievable for grounded answers."
        />
      ) : (
        <section className="card" aria-label="Documents">
          <div className="spread">
            <h2 style={{ fontSize: '1rem' }}>Your documents</h2>
            {polling ? (
              <span className="row muted" role="status">
                <span className="spinner" aria-hidden="true" /> processing…
              </span>
            ) : null}
          </div>
          <div className="table-wrap">
            <table className="data">
              <caption className="visually-hidden">Documents and their ingestion status</caption>
              <thead>
                <tr>
                  <th scope="col">Filename</th>
                  <th scope="col">Status</th>
                  <th scope="col">Source</th>
                  <th scope="col">Pages</th>
                  <th scope="col">Size</th>
                  <th scope="col">Added</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.data.map((document) => {
                  const meta = documentStatusMeta(document.status)
                  return (
                    <tr key={document.id}>
                      <td>
                        <Link to={`/documents/${document.id}`}>{document.filename}</Link>
                        {document.quarantine_state === 'clean' ? null : (
                          <Badge tone="warning"> {document.quarantine_state}</Badge>
                        )}
                      </td>
                      <td>
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                      </td>
                      <td>{document.source_type}</td>
                      <td>{document.page_count ?? '—'}</td>
                      <td>{formatBytes(document.size_bytes)}</td>
                      <td>{formatDateTime(document.created_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => setPendingDelete(document)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <InlineError error={remove.error} />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this document?"
        description={
          pendingDelete === null
            ? undefined
            : `"${pendingDelete.filename}" and its stored object will be removed. Answers that cited it will no longer resolve.`
        }
        confirmLabel="Delete document"
        destructive
        busy={remove.isPending}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete === null) return
          remove.mutate(pendingDelete.id, { onSuccess: () => setPendingDelete(null) })
        }}
      />
    </>
  )
}
