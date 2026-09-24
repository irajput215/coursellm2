import type { ReactNode } from 'react'

import { useConversations } from '@/api/hooks'
import type { ConversationSummary } from '@/api/types'
import { formatDateTime } from '@/lib/format'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'

/**
 * Server-persisted conversation history.
 *
 * The transcript lives on the server (`GET /chat/conversations`), so opening a
 * past conversation is a real resume: the next question continues it.
 */
export function ConversationSidebar({
  selectedId,
  onSelect,
  onNew,
}: {
  selectedId: string | null
  onSelect: (conversation: ConversationSummary) => void
  onNew: () => void
}): ReactNode {
  const conversations = useConversations()

  return (
    <aside className="card conversation-panel" aria-label="Conversation history">
      <div className="spread" style={{ marginBottom: '0.6rem' }}>
        <h2 style={{ fontSize: '1rem', margin: 0 }}>History</h2>
        <button type="button" className="btn btn-sm" onClick={onNew}>
          New
        </button>
      </div>

      {conversations.isPending ? (
        <SkeletonList rows={3} />
      ) : conversations.error !== null ? (
        <ErrorState
          error={conversations.error}
          title="Could not load conversations"
          onRetry={() => {
            void conversations.refetch()
          }}
        />
      ) : conversations.data.length === 0 ? (
        <EmptyState
          title="No conversations yet"
          description="Ask a question and the transcript will be saved here."
        />
      ) : (
        <ul className="nav-list">
          {conversations.data.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                className="nav-button"
                aria-current={selectedId === conversation.id ? 'true' : 'false'}
                onClick={() => onSelect(conversation)}
              >
                <span>
                  <span style={{ display: 'block', fontWeight: 600 }}>{conversation.title}</span>
                  <span className="muted" style={{ fontSize: '0.76rem' }}>
                    {formatDateTime(conversation.updated_at)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
