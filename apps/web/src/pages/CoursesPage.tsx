import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { useCourses, useCreateCourse, useDeleteCourse } from '@/api/hooks'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { InlineError, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import type { Course } from '@/api/types'

export function CoursesPage(): ReactNode {
  useDocumentTitle('Courses')
  const courses = useCourses()
  const createCourse = useCreateCourse()
  const deleteCourse = useDeleteCourse()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [description, setDescription] = useState('')
  const [pendingDelete, setPendingDelete] = useState<Course | null>(null)
  const nameId = useId()
  const codeId = useId()
  const descriptionId = useId()

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    if (name.trim() === '') return
    createCourse.mutate(
      {
        name: name.trim(),
        code: code.trim() === '' ? null : code.trim(),
        description: description.trim() === '' ? null : description.trim(),
      },
      {
        onSuccess: () => {
          setName('')
          setCode('')
          setDescription('')
        },
      },
    )
  }

  return (
    <>
      <PageHeader
        title="Courses"
        description="Each course is the scope retrieval is restricted to. Documents belong to a course."
      />

      <div className="card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem' }}>Create a course</h2>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor={nameId}>Name</label>
            <input
              id={nameId}
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={200}
            />
          </div>
          <div className="field">
            <label htmlFor={codeId}>Code</label>
            <input
              id={codeId}
              type="text"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              maxLength={40}
            />
            <span className="hint">Optional, for example CS-229.</span>
          </div>
          <div className="field">
            <label htmlFor={descriptionId}>Description</label>
            <textarea
              id={descriptionId}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={2000}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={createCourse.isPending}>
            {createCourse.isPending ? 'Creating…' : 'Create course'}
          </button>
        </form>
        <InlineError error={createCourse.error} />
      </div>

      {courses.isPending ? (
        <SkeletonList rows={3} />
      ) : courses.error !== null ? (
        <ErrorState
          error={courses.error}
          title="Could not load your courses"
          onRetry={() => {
            void courses.refetch()
          }}
        />
      ) : courses.data.length === 0 ? (
        <EmptyState title="No courses yet" description="Create your first course above." />
      ) : (
        <div className="card-grid">
          {courses.data.map((course) => (
            <article className="card" key={course.id}>
              <h2 style={{ fontSize: '1rem', marginBottom: '0.2rem' }}>
                <Link to={`/courses/${course.id}`}>{course.name}</Link>
              </h2>
              {course.code === null ? null : <p className="muted mono">{course.code}</p>}
              <p className="muted">
                {course.description === null || course.description === ''
                  ? 'No description.'
                  : course.description}
              </p>
              <div className="row">
                <Link className="btn btn-sm" to={`/courses/${course.id}`}>
                  Open
                </Link>
                <Link className="btn btn-sm" to={`/documents?course=${course.id}`}>
                  Documents
                </Link>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => setPendingDelete(course)}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <InlineError error={deleteCourse.error} />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this course?"
        description={
          pendingDelete === null
            ? undefined
            : `"${pendingDelete.name}" and everything in it — documents, chunks and citations — will be removed.`
        }
        confirmLabel="Delete course"
        destructive
        busy={deleteCourse.isPending}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete === null) return
          deleteCourse.mutate(pendingDelete.id, { onSuccess: () => setPendingDelete(null) })
        }}
      />
    </>
  )
}
