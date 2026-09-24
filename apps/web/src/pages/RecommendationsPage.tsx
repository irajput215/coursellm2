import { useId, useState, type ReactNode } from 'react'

import { useRecommendations } from '@/api/hooks'
import type { RecommendationItem, SourceTrust } from '@/api/types'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, PageHeader, type Tone } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { formatDecimal } from '@/lib/format'
import { DegradedBanner } from '@/features/chat/MessageBubble'

const TRUST_LABEL: Record<SourceTrust, string> = {
  official: 'Official',
  academic: 'Academic',
  community: 'Community',
  secondary: 'Secondary',
}

const TRUST_TONE: Record<SourceTrust, Tone> = {
  official: 'success',
  academic: 'info',
  community: 'neutral',
  secondary: 'warning',
}

function RecommendationCard({ item }: { item: RecommendationItem }): ReactNode {
  const { resource, explanation } = item
  const prerequisites =
    explanation.covered_gap_names.length > 0
      ? explanation.covered_gap_names.join(', ')
      : 'none listed by the API'

  return (
    <article className="card" aria-label={resource.title}>
      <h2 style={{ fontSize: '1.02rem', marginBottom: '0.2rem' }}>
        <a href={resource.url} target="_blank" rel="noopener noreferrer">
          {resource.title}
        </a>
      </h2>
      <p className="row muted" style={{ margin: '0 0 0.4rem', fontSize: '0.85rem' }}>
        <Badge tone="neutral">{resource.resource_type}</Badge>
        <span>difficulty {resource.difficulty}/5</span>
        {resource.duration_hours === null ? null : (
          <span>{formatDecimal(resource.duration_hours, 1)} h</span>
        )}
        {resource.is_free ? <Badge tone="success">free</Badge> : null}
        {item.personalised ? <Badge tone="accent">personalised</Badge> : null}
      </p>
      <p className="muted" style={{ margin: '0 0 0.5rem' }}>
        {resource.authors.length === 0 ? 'Unknown author' : resource.authors.join(', ')}
        {resource.publisher === null ? '' : ` · ${resource.publisher}`}
        {resource.year === null ? '' : ` · ${resource.year}`}
      </p>
      <p style={{ margin: '0 0 0.4rem' }}>
        <strong>Why recommended:</strong> {explanation.summary}
      </p>
      <dl className="citation-facts">
        <dt>Prerequisites addressed</dt>
        <dd>{prerequisites}</dd>
        <dt>Coverage</dt>
        <dd>{formatDecimal(explanation.coverage * 100, 0)}%</dd>
        <dt>Next step</dt>
        <dd>{explanation.next_step}</dd>
      </dl>
      <p className="row" style={{ marginTop: '0.6rem' }}>
        <a
          className="btn btn-sm"
          href={resource.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Badge tone={TRUST_TONE[resource.trust]}>{TRUST_LABEL[resource.trust]}</Badge>
          <span>Open source</span>
        </a>
      </p>
    </article>
  )
}

export function RecommendationsPage(): ReactNode {
  useDocumentTitle('Recommendations')
  const [difficultyMax, setDifficultyMax] = useState<string>('')
  const difficultyId = useId()
  const recommendations = useRecommendations(
    difficultyMax === '' ? {} : { difficultyMax: Number(difficultyMax) },
  )

  return (
    <>
      <PageHeader
        title="Recommendations"
        description="Each card names the gap it closes and why the ranking picked it. Nothing is recommended without a reason."
        actions={
          <>
            <label htmlFor={difficultyId} className="stat-label">
              Max difficulty
            </label>
            <select
              id={difficultyId}
              value={difficultyMax}
              onChange={(event) => setDifficultyMax(event.target.value)}
              style={{ width: 'auto' }}
            >
              <option value="">Any</option>
              {[1, 2, 3, 4, 5].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </>
        }
      />

      {recommendations.isPending ? (
        <SkeletonList rows={4} />
      ) : recommendations.error !== null ? (
        <ErrorState
          error={recommendations.error}
          title="Could not load recommendations"
          onRetry={() => {
            void recommendations.refetch()
          }}
        />
      ) : recommendations.data.recommendations.length === 0 ? (
        <EmptyState
          title="No recommendations right now"
          description="Recommendations are generated from measured gaps. Upload material and take a quiz so there is something to recommend against."
        />
      ) : (
        <div className="stack">
          <DegradedBanner degraded={recommendations.data.degraded} />
          {recommendations.data.personalised ? null : (
            <p className="banner banner-info" role="status">
              <span aria-hidden="true">ℹ</span>
              <span>
                These are catalogue results, not personalised ones: the API did not have enough of
                your progress to rank against.
              </span>
            </p>
          )}

          {recommendations.data.gaps.length === 0 ? null : (
            <section className="card" aria-label="Gaps being addressed">
              <h2 style={{ fontSize: '1rem' }}>Gaps this set addresses</h2>
              <ul className="concept-list">
                {recommendations.data.gaps.map((gap) => (
                  <li key={gap.concept_id}>
                    <span>{gap.name}</span>
                    <span className="muted">
                      difficulty {gap.difficulty} ·{' '}
                      {gap.never_assessed
                        ? 'never assessed'
                        : `mastery ${formatDecimal(gap.mastery * 100, 0)}%`}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="card-grid">
            {recommendations.data.recommendations.map((item) => (
              <RecommendationCard key={item.resource.id} item={item} />
            ))}
          </div>
        </div>
      )}
    </>
  )
}
