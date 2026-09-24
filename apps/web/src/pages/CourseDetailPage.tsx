import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useCourse } from '@/api/hooks'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { documentStatusMeta } from '@/features/documents/status'
import { formatBytes, formatDate } from '@/lib/format'

export function CourseDetailPage(): ReactNode {
  const { courseId } = useParams<{ courseId: string }>()
  const course = useCourse(courseId)
  useDocumentTitle(course.data?.name ?? 'Course')

  return (
    <>
      <PageHeader
        title={course.data?.name ?? 'Course'}
        description={course.data?.description ?? undefined}
        actions={
          <>
            <Link className="btn" to="/courses">
              All courses
            </Link>
            {courseId === undefined ? null : (
              <Link className="btn btn-primary" to={`/documents?course=${courseId}`}>
                Upload material
              </Link>
            )}
          </>
        }
      />

      {course.isPending ? (
        <SkeletonList rows={3} />
      ) : course.error !== null ? (
        <ErrorState
          error={course.error}
          title="Could not load this course"
          onRetry={() => {
            void course.refetch()
          }}
        />
      ) : (
        <div className="stack">
          <section className="card" aria-label="Course details">
            <dl className="citation-facts">
              <dt>Code</dt>
              <dd>{course.data.code ?? '—'}</dd>
              <dt>Created</dt>
              <dd>{formatDate(String(course.data.created_at))}</dd>
              <dt>Course id</dt>
              <dd>{course.data.id}</dd>
            </dl>
          </section>

          <section className="card" aria-label="Documents in this course">
            <h2 style={{ fontSize: '1rem' }}>Documents</h2>
            {course.data.documents.length === 0 ? (
              <EmptyState
                title="No documents in this course"
                description="Upload a lecture, paper or syllabus to make it retrievable."
                action={
                  courseId === undefined ? undefined : (
                    <Link className="btn btn-primary" to={`/documents?course=${courseId}`}>
                      Upload material
                    </Link>
                  )
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <caption className="visually-hidden">Documents in this course</caption>
                  <thead>
                    <tr>
                      <th scope="col">Filename</th>
                      <th scope="col">Status</th>
                      <th scope="col">Pages</th>
                      <th scope="col">Size</th>
                      <th scope="col">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {course.data.documents.map((document) => {
                      const meta = documentStatusMeta(document.status)
                      return (
                        <tr key={document.id}>
                          <td>
                            <Link to={`/documents/${document.id}`}>{document.filename}</Link>
                          </td>
                          <td>
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                          </td>
                          <td>{document.page_count ?? '—'}</td>
                          <td>{formatBytes(document.size_bytes)}</td>
                          <td>{document.source_type}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  )
}
