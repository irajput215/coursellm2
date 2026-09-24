import { useCallback, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { apiRequest, errorMessage, isApiError } from '@/api/client'
import { queryKeys } from '@/api/keys'
import type {
  AnyCitation,
  ChatEngine,
  ConversationDetail,
  ConversationSummary,
  ProposedAction,
} from '@/api/types'

import { streamChat } from './streamChat'

export type TurnStatus = 'streaming' | 'complete' | 'error'

export interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: AnyCitation[]
  degraded: string[]
  grounded: boolean | null
  proposedActions: ProposedAction[]
  status: TurnStatus
  error: string | null
  requestId: string | null
  /** The question this turn answers; kept so a failed turn can be retried. */
  question: string
  cancelled: boolean
}

let fallbackCounter = 0

function newId(prefix: string): string {
  const globalCrypto = globalThis.crypto
  if (globalCrypto !== undefined && typeof globalCrypto.randomUUID === 'function') {
    return `${prefix}-${globalCrypto.randomUUID()}`
  }
  fallbackCounter += 1
  return `${prefix}-${String(fallbackCounter)}`
}

function userTurn(question: string): ChatTurn {
  return {
    id: newId('user'),
    role: 'user',
    content: question,
    citations: [],
    degraded: [],
    grounded: null,
    proposedActions: [],
    status: 'complete',
    error: null,
    requestId: null,
    question,
    cancelled: false,
  }
}

function assistantTurn(question: string): ChatTurn {
  return {
    id: newId('assistant'),
    role: 'assistant',
    content: '',
    citations: [],
    degraded: [],
    grounded: null,
    proposedActions: [],
    status: 'streaming',
    error: null,
    requestId: null,
    question,
    cancelled: false,
  }
}

function isAbort(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name?: unknown }).name === 'AbortError'
  )
}

export interface ChatStreamController {
  turns: ChatTurn[]
  isStreaming: boolean
  conversationId: string | null
  send: (question: string) => void
  retry: () => void
  cancel: () => void
  startNew: () => void
  loadConversation: (detail: ConversationDetail) => void
  adoptConversationId: (id: string) => void
}

/**
 * One streaming turn at a time, rendered as it arrives.
 *
 * After a turn completes the persisted assistant message is reconciled from
 * `GET /chat/conversations/{id}`: the SSE contract does not carry `grounded` or
 * `degraded`, and the UI must not invent them. The stream itself drives the
 * visible text; reconciliation only fills in what the stream omits.
 */
export function useChatStream(options: {
  engine: ChatEngine
  courseId: string | null
}): ChatStreamController {
  const { engine, courseId } = options
  const queryClient = useQueryClient()
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const conversationIdRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const patchTurn = useCallback((id: string, patch: Partial<ChatTurn>): void => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
    )
  }, [])

  const adoptConversationId = useCallback((id: string): void => {
    conversationIdRef.current = id
    setConversationId(id)
  }, [])

  const reconcile = useCallback(
    async (assistantId: string): Promise<void> => {
      try {
        const conversations = await queryClient.fetchQuery({
          queryKey: queryKeys.chat.conversations,
          queryFn: () => apiRequest<ConversationSummary[]>('/chat/conversations'),
        })
        const resolvedId = conversationIdRef.current ?? conversations[0]?.id ?? null
        if (resolvedId === null) return
        if (conversationIdRef.current === null) adoptConversationId(resolvedId)
        const detail = await queryClient.fetchQuery({
          queryKey: queryKeys.chat.conversation(resolvedId),
          queryFn: () =>
            apiRequest<ConversationDetail>(`/chat/conversations/${resolvedId}`),
        })
        const lastAssistant = [...detail.messages]
          .reverse()
          .find((message) => message.role === 'assistant')
        if (lastAssistant === undefined) return
        patchTurn(assistantId, {
          degraded: lastAssistant.degraded,
          grounded: lastAssistant.grounded,
          citations: lastAssistant.citations,
        })
      } catch {
        // The answer is already on screen; reconciliation is best-effort.
      }
    },
    [adoptConversationId, patchTurn, queryClient],
  )

  const runTurn = useCallback(
    async (question: string, replaceTurnId: string | null): Promise<void> => {
      const assistant = assistantTurn(question)
      setTurns((current) => {
        const kept = replaceTurnId === null ? current : current.filter((t) => t.id !== replaceTurnId)
        return replaceTurnId === null
          ? [...kept, userTurn(question), assistant]
          : [...kept, assistant]
      })
      setIsStreaming(true)
      const controller = new AbortController()
      abortRef.current = controller
      let accumulated = ''

      try {
        for await (const event of streamChat({
          question,
          engine,
          courseId,
          conversationId: conversationIdRef.current,
          signal: controller.signal,
        })) {
          if (event.type === 'token') {
            accumulated += event.text
            patchTurn(assistant.id, { content: accumulated })
          } else if (event.type === 'citations') {
            patchTurn(assistant.id, { citations: event.citations })
          } else if (event.type === 'done') {
            patchTurn(assistant.id, { proposedActions: event.proposedActions })
          } else {
            patchTurn(assistant.id, {
              status: 'error',
              error: event.detail,
              requestId: event.requestId,
            })
            setIsStreaming(false)
            return
          }
        }
        patchTurn(assistant.id, { status: 'complete' })
        void queryClient.invalidateQueries({ queryKey: queryKeys.chat.conversations })
        await reconcile(assistant.id)
      } catch (error) {
        if (isAbort(error)) {
          patchTurn(assistant.id, {
            status: accumulated === '' ? 'error' : 'complete',
            cancelled: true,
            error: accumulated === '' ? 'Cancelled before any text arrived.' : null,
          })
        } else {
          patchTurn(assistant.id, {
            status: 'error',
            error: isApiError(error) ? error.detail : errorMessage(error),
            requestId: isApiError(error) ? error.request_id : null,
          })
        }
      } finally {
        abortRef.current = null
        setIsStreaming(false)
      }
    },
    [courseId, engine, patchTurn, queryClient, reconcile],
  )

  const send = useCallback(
    (question: string): void => {
      const trimmed = question.trim()
      if (trimmed === '' || isStreaming) return
      void runTurn(trimmed, null)
    },
    [isStreaming, runTurn],
  )

  const retry = useCallback((): void => {
    if (isStreaming) return
    const failed = [...turns].reverse().find((turn) => turn.status === 'error')
    if (failed === undefined) return
    void runTurn(failed.question, failed.id)
  }, [isStreaming, runTurn, turns])

  const cancel = useCallback((): void => {
    abortRef.current?.abort()
  }, [])

  const startNew = useCallback((): void => {
    abortRef.current?.abort()
    conversationIdRef.current = null
    setConversationId(null)
    setTurns([])
  }, [])

  const loadConversation = useCallback((detail: ConversationDetail): void => {
    abortRef.current?.abort()
    conversationIdRef.current = detail.id
    setConversationId(detail.id)
    const restored: ChatTurn[] = []
    for (const message of detail.messages) {
      if (message.role !== 'user' && message.role !== 'assistant') continue
      restored.push({
        id: message.id,
        role: message.role,
        content: message.content,
        citations: message.citations,
        degraded: message.degraded,
        grounded: message.grounded,
        proposedActions: [],
        status: 'complete',
        error: null,
        requestId: null,
        question: message.role === 'user' ? message.content : '',
        cancelled: false,
      })
    }
    setTurns(restored)
  }, [])

  return {
    turns,
    isStreaming,
    conversationId,
    send,
    retry,
    cancel,
    startNew,
    loadConversation,
    adoptConversationId,
  }
}
