import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { useCourses, useConversation } from '@/api/hooks'
import type { ChatEngine, ConversationSummary } from '@/api/types'
import { EmptyState, ErrorState } from '@/components/states'
import { PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { ChatComposer } from '@/features/chat/ChatComposer'
import { ConversationSidebar } from '@/features/chat/ConversationSidebar'
import { FollowUpActions } from '@/features/chat/FollowUpActions'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { questionForTurn, suggestedFollowUps } from '@/features/chat/prompts'
import { useChatStream, type ChatTurn } from '@/features/chat/useChatStream'

export function ChatPage(): ReactNode {
  useDocumentTitle('Chat')
  const courses = useCourses()
  const [engine, setEngine] = useState<ChatEngine>('agent')
  const [courseId, setCourseId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const chat = useChatStream({ engine, courseId })
  const conversation = useConversation(selectedId ?? undefined)
  const { loadConversation } = chat

  useEffect(() => {
    if (conversation.data !== undefined) loadConversation(conversation.data)
  }, [conversation.data, loadConversation])

  const lastAnswerIndex = useMemo(() => {
    for (let index = chat.turns.length - 1; index >= 0; index -= 1) {
      const turn = chat.turns[index]
      if (turn !== undefined && turn.role === 'assistant' && turn.content !== '') return index
    }
    return -1
  }, [chat.turns])

  const lastAnswer: ChatTurn | undefined =
    lastAnswerIndex === -1 ? undefined : chat.turns[lastAnswerIndex]

  const handleSelect = (conversationSummary: ConversationSummary): void => {
    setSelectedId(conversationSummary.id)
  }

  return (
    <div className="chat-layout">
      <ConversationSidebar
        selectedId={chat.conversationId}
        onSelect={handleSelect}
        onNew={() => {
          setSelectedId(null)
          chat.startNew()
        }}
      />

      <section aria-label="Conversation">
        <PageHeader
          title="Chat"
          description="Grounded answers over your own material, with the sources each answer used."
        />

        {conversation.error !== null ? (
          <ErrorState
            error={conversation.error}
            title="Could not open that conversation"
            onRetry={() => {
              void conversation.refetch()
            }}
          />
        ) : null}

        <ul
          className="message-list"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          aria-label="Conversation transcript"
        >
          {chat.turns.length === 0 ? (
            <li>
              <EmptyState
                title="Ask your first question"
                description="Every answer is grounded in the material you uploaded and lists the sources it used. If the evidence does not support an answer, the assistant says so."
              />
            </li>
          ) : (
            chat.turns.map((turn) => (
              <MessageBubble
                key={turn.id}
                turn={turn}
                onRetry={chat.retry}
                canRetry={!chat.isStreaming}
              />
            ))
          )}
        </ul>

        {lastAnswer === undefined ? null : (
          <FollowUpActions
            question={questionForTurn(chat.turns, lastAnswerIndex)}
            suggestions={suggestedFollowUps(lastAnswer)}
            disabled={chat.isStreaming}
            onAction={chat.send}
          />
        )}

        <ChatComposer
          onSend={chat.send}
          onCancel={chat.cancel}
          isStreaming={chat.isStreaming}
          engine={engine}
          onEngineChange={setEngine}
          courses={courses.data ?? []}
          courseId={courseId}
          onCourseChange={setCourseId}
        />
      </section>
    </div>
  )
}
