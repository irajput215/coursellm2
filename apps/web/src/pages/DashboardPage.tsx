import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { useCourses, useProgressSummary } from '@/api/hooks'
import { DegradedBanner } from '@/features/chat/MessageBubble'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, PageHeader, StatCard } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { formatDateTime, formatDecimal, formatPercent, shortId } from '@/lib/format'

/**
 * The dashboard shows only values the API returned.
 *
 * The client this replaces rendered hardcoded engagement counts and an invented
 * average evaluation score as if they were measurements. Every number below
 * comes from `GET /progress/summary` (or is an explicit empty state).
 */
export function DashboardPage(): ReactNode {
  useDocumentTitle('Dashboard')
  const summary = useProgressSummary()
  const courses = useCourses()

  const attemptsLogged =
    summary.data === undefined
      ? 0
      : Object.values(summary.data.attempt_counts).reduce((total, count) => total + count, 0)
  const conceptsTracked = summary.data === undefined ? 0 : Object.keys(summary.data.mastery).length
  const isEmpty =
    summary.data !== undefined &&
    conceptsTracked === 0 &&
    attemptsLogged === 0 &&
    summary.data.recent_attempts.length === 0 &&
    summary.data.next_action === null

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Your position, from the progress API. Nothing here is a placeholder."
        actions={
          <Link className="btn btn-primary" to="/chat">
            Ask a question
          </Link>
        }
      />

      {summary.isPending ? (
        <SkeletonList rows={4} />
      ) : summary.error !== null ? (
        <ErrorState
          error={summary.error}
          title="Could not load your progress"
          onRetry={() => {
            void summary.refetch()
          }}
        />
      ) : isEmpty ? (
        <EmptyState
          title="Nothing to summarise yet"
          description="Upload material for a course and ask your first question — progress appears here once there is something to measure."
          action={
            <Link className="btn btn-primary" to="/documents">
              Add course material
            </Link>
          }
        />
      ) : (
        <div className="stack">
          <DegradedBanner degraded={summary.data.degraded} />

          <section aria-label="Progress at a glance">
            <div className="stat-grid">
              <StatCard
                label="Concepts tracked"
                value={conceptsTracked}
                hint="from your mastery record"
              />
              <StatCard label="Attempts logged" value={attemptsLogged} hint="quiz answers recorded" />
              <StatCard label="Weak concepts" value={summary.data.weak_concepts.length} />
              <StatCard label="Stale concepts" value={summary.data.stale_concepts.length} />
              <StatCard
                label="Velocity"
                value={summary.data.velocity === null ? '—' : formatDecimal(summary.data.velocity, 2)}
                hint={summary.data.velocity === null ? 'not enough history yet' : 'concepts per day'}
              />
            </div>
          </section>

          <div className="card-grid">
            <section className="card" aria-label="Next action">
              <h2 style={{ fontSize: '1rem' }}>Next action</h2>
              {summary.data.next_action === null ? (
                <p className="muted" style={{ margin: 0 }}>
                  The API did not return a next action. That usually means there is not enough
                  evidence yet — ask a question or take a quiz.
                </p>
              ) : (
                <>
                  <p style={{ margin: '0 0 0.25rem', fontWeight: 650 }}>
                    {summary.data.next_action.title}
                  </p>
                  <p className="muted" style={{ margin: '0 0 0.5rem' }}>
                    {summary.data.next_action.rationale}
                  </p>
                  <div className="row">
                    <Badge tone="info">{summary.data.next_action.kind}</Badge>
                    {summary.data.next_action.roadmap_id === null ? null : (
                      <Link
                        className="btn btn-sm"
                        to={`/roadmap?roadmap=${summary.data.next_action.roadmap_id}`}
                      >
                        Open the plan
                      </Link>
                    )}
                    <Link className="btn btn-sm" to="/quiz">
                      Take a quiz
                    </Link>
                  </div>
                </>
              )}
            </section>

            <section className="card" aria-label="Current position">
              <h2 style={{ fontSize: '1rem' }}>Current position</h2>
              {summary.data.current_position === null ? (
                <p className="muted" style={{ margin: 0 }}>
                  No active roadmap step.
                </p>
              ) : (
                <>
                  <p style={{ margin: '0 0 0.25rem', fontWeight: 650 }}>
                    {summary.data.current_position.title}
                  </p>
                  <p className="muted" style={{ margin: 0, fontSize: '0.86rem' }}>
                    Step {summary.data.current_position.order_index + 1} · revision{' '}
                    {summary.data.current_position.revision} · {summary.data.current_position.status}
                  </p>
                  <Link
                    className="btn btn-sm"
                    style={{ marginTop: '0.5rem' }}
                    to={`/roadmap?roadmap=${summary.data.current_position.roadmap_id}`}
                  >
                    Continue
                  </Link>
                </>
              )}
            </section>

            <section className="card" aria-label="Courses">
              <h2 style={{ fontSize: '1rem' }}>Courses</h2>
              {courses.isPending ? (
                <p className="muted" style={{ margin: 0 }}>
                  Loading…
                </p>
              ) : courses.error !== null ? (
                <p className="muted" style={{ margin: 0 }}>
                  Course list unavailable.
                </p>
              ) : courses.data.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>
                  No courses yet. <Link to="/courses">Create one</Link>.
                </p>
              ) : (
                <ul className="concept-list">
                  {courses.data.slice(0, 5).map((course) => (
                    <li key={course.id}>
                      <Link to={`/courses/${course.id}`}>{course.name}</Link>
                      <span className="muted">{course.code ?? ''}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section className="card" aria-label="Recent attempts">
            <h2 style={{ fontSize: '1rem' }}>Recent attempts</h2>
            {summary.data.recent_attempts.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No quiz attempts recorded yet.
              </p>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <caption className="visually-hidden">Your most recent quiz attempts</caption>
                  <thead>
                    <tr>
                      <th scope="col">When</th>
                      <th scope="col">Concept</th>
                      <th scope="col">Score</th>
                      <th scope="col">Misconceptions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.data.recent_attempts.map((attempt) => (
                      <tr key={attempt.id}>
                        <td>{formatDateTime(attempt.created_at)}</td>
                        <td className="mono">
                          {attempt.concept_ids.length === 0
                            ? '—'
                            : attempt.concept_ids.map(shortId).join(', ')}
                        </td>
                        <td>{formatPercent(attempt.score)}</td>
                        <td>{attempt.misconceptions.length}</td>
                      </tr>
                    ))}
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
