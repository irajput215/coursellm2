import type { ReactNode } from 'react'

import { DERIVED_ACTIONS } from './prompts'

/**
 * The action row under an answer plus citation-derived follow-up suggestions.
 *
 * Both send a *derived* prompt: the action verbs are fixed UI, but the question
 * they wrap is the student's own, and the suggestions only exist when the API
 * returned citations to derive them from.
 */
export function FollowUpActions({
  question,
  suggestions,
  disabled,
  onAction,
}: {
  question: string
  suggestions: readonly string[]
  disabled: boolean
  onAction: (prompt: string) => void
}): ReactNode {
  if (question === '') return null
  return (
    <div className="stack" style={{ marginTop: '0.75rem' }}>
      <div className="row" role="group" aria-label="Follow-up actions">
        {DERIVED_ACTIONS.map((action) => (
          <button
            key={action.id}
            type="button"
            className="btn btn-sm"
            disabled={disabled}
            onClick={() => onAction(action.build(question))}
          >
            {action.label}
          </button>
        ))}
      </div>
      {suggestions.length === 0 ? null : (
        <div>
          <p className="stat-label" style={{ margin: 0 }}>
            Suggested follow-ups
          </p>
          <div className="row">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="btn btn-sm btn-ghost"
                disabled={disabled}
                onClick={() => onAction(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
