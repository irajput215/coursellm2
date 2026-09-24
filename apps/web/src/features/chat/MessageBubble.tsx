import type { ReactNode } from 'react'

import { CitationChips } from '@/components/CitationChips'
import { Markdown } from '@/components/Markdown'
import { humaniseToken } from '@/lib/format'
import { ProposalCard } from './ProposalCard'
import type { ChatTurn } from './useChatStream'

export function DegradedBanner({ degraded }: { degraded: readonly string[] }): ReactNode {
  if (degraded.length === 0) return null
  return (
    <p className="banner banner-warning" role="status">
      <span aria-hidden="true">⚠</span>
      <span>
        Answer quality is reduced:{' '}
        {degraded.map((reason, index) => (
          <span key={reason}>
            {index > 0 ? ', ' : ''}
            <code className="request-id">{reason}</code>
            <span className="muted"> ({humaniseToken(reason)})</span>
          </span>
        ))}
      </span>
    </p>
  )
}

function AssistantMeta({ turn }: { turn: ChatTurn }): ReactNode {
  const flags: string[] = []
  if (turn.grounded === true) flags.push('grounded in your material')
  if (turn.grounded === false) flags.push('not grounded')
  if (turn.cancelled) flags.push('cancelled')
  if (flags.length === 0) return null
  return <p className="message-meta">{flags.join(' · ')}</p>
}

/**
 * One rendered turn.
 *
 * An error turn is deliberately *not* styled like an answer: it is a red
 * container with the failure and a retry, and any partial text is labelled
 * incomplete. A failed turn must never be mistaken for a response.
 */
export function MessageBubble({
  turn,
  onRetry,
  canRetry,
}: {
  turn: ChatTurn
  onRetry?: (() => void) | undefined
  canRetry?: boolean
}): ReactNode {
  if (turn.role === 'user') {
    return (
      <li className="message message-user">
        <div className="message-body">
          <p className="stat-label" style={{ margin: 0 }}>
            You
          </p>
          <p style={{ margin: '0.2rem 0 0', whiteSpace: 'pre-wrap' }}>{turn.content}</p>
        </div>
      </li>
    )
  }

  if (turn.status === 'error') {
    return (
      <li className="message message-error">
        <div className="message-body" role="alert">
          <p style={{ margin: 0, fontWeight: 650, color: 'var(--danger)' }}>
            <span aria-hidden="true">⚠ </span>
            This answer failed
          </p>
          <p style={{ margin: '0.3rem 0 0' }}>{turn.error ?? 'The response could not be completed.'}</p>
          {turn.requestId === null ? null : (
            <p className="muted" style={{ margin: '0.3rem 0 0', fontSize: '0.82rem' }}>
              request id <code className="request-id">{turn.requestId}</code>
            </p>
          )}
          {turn.content === '' ? null : (
            <>
              <p className="stat-label" style={{ margin: '0.6rem 0 0' }}>
                Partial response (incomplete)
              </p>
              <Markdown content={turn.content} />
            </>
          )}
          {onRetry === undefined || canRetry !== true ? null : (
            <div className="row" style={{ marginTop: '0.5rem' }}>
              <button type="button" className="btn btn-sm" onClick={onRetry}>
                Retry
              </button>
            </div>
          )}
        </div>
      </li>
    )
  }

  return (
    <li className={turn.status === 'streaming' ? 'message message-streaming' : 'message'}>
      <div className="message-body">
        <p className="stat-label" style={{ margin: 0 }}>
          CourseLLM
        </p>
        {turn.content === '' && turn.status === 'streaming' ? (
          <p className="muted" style={{ margin: '0.3rem 0 0' }}>
            Searching your course material…
          </p>
        ) : (
          <Markdown content={turn.content} />
        )}
        {turn.status === 'streaming' ? (
          <span className="cursor" aria-hidden="true">
            ▌
          </span>
        ) : null}
        <DegradedBanner degraded={turn.degraded} />
        <CitationChips citations={turn.citations} />
        {turn.proposedActions.map((action) => (
          <ProposalCard key={action.token} action={action} />
        ))}
        <AssistantMeta turn={turn} />
      </div>
    </li>
  )
}
