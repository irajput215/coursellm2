/**
 * The streaming chat transport.
 *
 * `POST /chat/stream` is a POST, so `EventSource` cannot be used: the response
 * is read with `fetch` + a stream reader and decoded as SSE. The server emits
 * `token` … `citations` … `done`, or `error`.
 */
import { readSseEvents, streamRequest } from '@/api/client'
import type { AnyCitation, ChatEngine, ProposedAction } from '@/api/types'

export interface ChatTokenEvent {
  type: 'token'
  text: string
}

export interface ChatCitationsEvent {
  type: 'citations'
  citations: AnyCitation[]
}

export interface ChatDoneEvent {
  type: 'done'
  proposedActions: ProposedAction[]
}

export interface ChatErrorEvent {
  type: 'error'
  detail: string
  requestId: string | null
}

export type ChatStreamEvent =
  | ChatTokenEvent
  | ChatCitationsEvent
  | ChatDoneEvent
  | ChatErrorEvent

export interface StreamChatInput {
  question: string
  courseId?: string | null
  conversationId?: string | null
  engine: ChatEngine
  signal?: AbortSignal
}

function safeParse(data: string): unknown {
  try {
    return JSON.parse(data) as unknown
  } catch {
    return null
  }
}

function readCitations(payload: unknown): AnyCitation[] {
  if (!Array.isArray(payload)) return []
  return payload.filter(
    (item): item is AnyCitation =>
      typeof item === 'object' && item !== null && 'citation_id' in item && 'filename' in item,
  )
}

function readProposedActions(payload: unknown): ProposedAction[] {
  if (typeof payload !== 'object' || payload === null || !('proposed_actions' in payload)) return []
  const actions = (payload as { proposed_actions?: unknown }).proposed_actions
  if (!Array.isArray(actions)) return []
  return actions.filter(
    (item): item is ProposedAction =>
      typeof item === 'object' && item !== null && 'token' in item && 'action' in item,
  )
}

export async function* streamChat(input: StreamChatInput): AsyncGenerator<ChatStreamEvent> {
  const body = {
    question: input.question,
    engine: input.engine,
    ...(input.courseId === null || input.courseId === undefined
      ? {}
      : { course_id: input.courseId }),
    ...(input.conversationId === null || input.conversationId === undefined
      ? {}
      : { conversation_id: input.conversationId }),
  }

  const response = await streamRequest(
    '/chat/stream',
    body,
    input.signal === undefined ? undefined : input.signal,
  )
  const headerRequestId = response.headers.get('X-Request-Id')

  for await (const event of readSseEvents(response, input.signal)) {
    const payload = safeParse(event.data)
    if (event.event === 'token') {
      if (typeof payload === 'object' && payload !== null && 'text' in payload) {
        const text = (payload as { text?: unknown }).text
        if (typeof text === 'string') yield { type: 'token', text }
      }
    } else if (event.event === 'citations') {
      yield { type: 'citations', citations: readCitations(payload) }
    } else if (event.event === 'done') {
      yield { type: 'done', proposedActions: readProposedActions(payload) }
    } else if (event.event === 'error') {
      const detail =
        typeof payload === 'object' && payload !== null && 'detail' in payload
          ? (payload as { detail?: unknown }).detail
          : null
      yield {
        type: 'error',
        detail:
          typeof detail === 'string'
            ? detail
            : 'The answer stream failed before it completed.',
        requestId: headerRequestId,
      }
    }
  }
}
