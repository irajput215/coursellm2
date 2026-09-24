import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { useAttempts, useProgressSummary } from '@/api/hooks'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, PageHeader, StatCard } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { formatDateTime, formatDecimal, formatPercent, shortId } from '@/lib/format'
import { DegradedBanner } from '@/features/chat/MessageBubble'

const PAGE_SIZE = 20

export function ProgressPage(): ReactNode {
  useDocumentTitle('Progress')
  const [offset, setOffset] = useState(0)
  const summary = useProgressSummary()
  const attempts = useAttempts({ limit: PAGE_SIZE, offset })

  const masteryEntries =
    summary.data === undefined
      ? []
      : Object.entries(summary.data.mastery).sort(([, a], [, b]) => a - b)
  const attemptTotal = summary.data === undefined ? 0 : attempts.data?.total ?? 0

  return (
    <>
      <PageHeader
        title="Progress"
        description="Mastery, weak and stale concepts, velocity and your attempt history — every number from the progress API."
        actions={
          <Link className="btn btn-primary" to="/quiz">
            Take a quiz
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
      ) : (
        <div className="stack">
          <DegradedBanner degraded={summary.data.degraded} />

          <div className="stat-grid">
            <StatCard label="Concepts tracked" value={Object.keys(summary.data.mastery).length} />
            <StatCard label="Weak concepts" value={summary.data.weak_concepts.length} />
            <StatCard label="Stale concepts" value={summary.data.stale_concepts.length} />
            <StatCard
              label="Velocity"
              value={summary.data.velocity === null ? '—' : formatDecimal(summary.data.velocity, 2)}
              hint={summary.data.velocity === null ? 'not enough history yet' : 'concepts per day'}
            />
            <StatCard
              label="Attempts recorded"
              value={attempts.data?.total ?? '—'}
              hint={`${PAGE_SIZE} per page`}
            />
          </div>

          <section className="card" aria-label="Mastery by concept">
            <h2 style={{ fontSize: '1rem' }}>Mastery</h2>
            {masteryEntries.length === 0 ? (
              <EmptyState
                title="No mastery recorded"
                description="Take a quiz and mastery is recorded per concept."
              />
            ) : (
              <ul className="concept-list">
                {masteryEntries.map(([conceptId, value]) => (
                  <li key={conceptId}>
                    <span className="mono">{shortId(conceptId)}</span>
                    <span style={{ minWidth: '7rem', flex: 1 }}>
                      <span className="meter" role="presentation">
                        <span style={{ width: `${String(Math.round(value * 100))}%` }} />
                      </span>
                    </span>
                    <span>{formatPercent(value)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="card-grid">
            <section className="card" aria-label="Weak concepts">
              <h2 style={{ fontSize: '1rem' }}>Weak concepts</h2>
              {summary.data.weak_concepts.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>
                  None flagged.
                </p>
              ) : (
                <ul className="row" style={{ listStyle: 'none', padding: 0 }}>
                  {summary.data.weak_concepts.map((conceptId) => (
                    <li key={conceptId}>
                      <Badge tone="warning">{shortId(conceptId)}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="card" aria-label="Stale concepts">
              <h2 style={{ fontSize: '1rem' }}>Stale concepts</h2>
              {summary.data.stale_concepts.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>
                  None flagged.
                </p>
              ) : (
                <ul className="row" style={{ listStyle: 'none', padding: 0 }}>
                  {summary.data.stale_concepts.map((conceptId) => (
                    <li key={conceptId}>
                      <Badge tone="info">{shortId(conceptId)}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}

      <section className="card" style={{ marginTop: '1rem' }} aria-label="Attempt history">
        <div className="spread">
          <h2 style={{ fontSize: '1rem' }}>Attempt history</h2>
          <span className="muted">
            {attempts.data === undefined
              ? ''
              : `${attempts.data.offset + 1}–${Math.min(
                  attempts.data.offset + attempts.data.limit,
                  attemptTotal,
                )} of ${attemptTotal}`}
          </span>
        </div>

        {attempts.isPending ? (
          <SkeletonList rows={3} />
        ) : attempts.error !== null ? (
          <ErrorState
            error={attempts.error}
            title="Could not load your attempts"
            onRetry={() => {
              void attempts.refetch()
            }}
          />
        ) : attempts.data.items.length === 0 ? (
          <EmptyState
            title="No attempts yet"
            description="Answer a quiz question and it will be recorded here with its rubric."
          />
        ) : (
          <>
            <div className="table-wrap">
              <table className="data">
                <caption className="visually-hidden">Your quiz attempt history</caption>
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Item</th>
                    <th scope="col">Concepts</th>
                    <th scope="col">Score</th>
                    <th scope="col">Rubric</th>
                    <th scope="col">Misconceptions</th>
                  </tr>
                </thead>
                <tbody>
                  {attempts.data.items.map((attempt) => (
                    <tr key={attempt.id}>
                      <td>{formatDateTime(attempt.created_at)}</td>
                      <td className="mono">{shortId(attempt.item_id)}</td>
                      <td className="mono">
                        {attempt.concept_ids.length === 0
                          ? '—'
                          : attempt.concept_ids.map(shortId).join(', ')}
                      </td>
                      <td>{formatPercent(attempt.score)}</td>
                      <td>{attempt.rubric.length} criteria</td>
                      <td>{attempt.misconceptions.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ marginTop: '0.6rem' }}>
              <button
                type="button"
                className="btn btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={offset + PAGE_SIZE >= attemptTotal}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </>
        )}
      </section>
    </>
  )
}
