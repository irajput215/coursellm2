import { useState, type ReactNode } from 'react'

import { useConfirmProposal } from '@/api/hooks'
import type { ProposedAction } from '@/api/types'
import { formatDateTime, humaniseToken } from '@/lib/format'
import { InlineError } from '@/components/ui'

/**
 * A consequential write the agent proposed but did not perform.
 *
 * The model never holds an execution capability: the signed token comes back to
 * the server only after the student presses Confirm, and the server re-validates
 * signature, expiry, tenant and arguments before anything happens.
 */
export function ProposalCard({ action }: { action: ProposedAction }): ReactNode {
  const confirm = useConfirmProposal()
  const [expired, setExpired] = useState(false)
  const outcome = confirm.data
  const expiresAt = formatDateTime(new Date(action.expires_at * 1000).toISOString())

  const handleConfirm = (): void => {
    if (action.expires_at * 1000 <= Date.now()) {
      setExpired(true)
      return
    }
    confirm.mutate(action.token)
  }

  return (
    <div className="proposal">
      <p style={{ margin: 0, fontWeight: 650 }}>
        <span aria-hidden="true">✋ </span>
        Proposed action: {humaniseToken(action.action)}
      </p>
      <p className="muted" style={{ margin: '0.25rem 0 0.5rem' }}>
        {action.rationale}
      </p>
      {action.preview === '' ? null : <pre className="proposal-preview">{action.preview}</pre>}
      <p className="muted" style={{ margin: '0.4rem 0 0', fontSize: '0.8rem' }}>
        Expires {expiresAt}
      </p>
      <div className="row" style={{ marginTop: '0.6rem' }}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={expired || confirm.isPending}
          onClick={handleConfirm}
        >
          {confirm.isPending ? 'Confirming…' : 'Confirm'}
        </button>
        {expired ? <span className="muted">This proposal has expired.</span> : null}
      </div>
      {outcome === undefined ? null : (
        <p role="status" style={{ margin: '0.5rem 0 0' }}>
          {outcome.status === 'ok'
            ? `Done — ${humaniseToken(outcome.tool)} completed.`
            : `Refused — ${outcome.detail ?? outcome.status}`}
        </p>
      )}
      <InlineError error={confirm.error} />
    </div>
  )
}
