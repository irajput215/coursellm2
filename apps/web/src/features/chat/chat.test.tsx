import { HttpResponse, http } from 'msw'
import { act } from 'react'
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ChatPage } from '@/pages/ChatPage'
import { makeConversationDetail, sseChunk } from '@/test/handlers'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

interface StreamHandle {
  controller: ReadableStreamDefaultController<Uint8Array> | null
}

const encoder = new TextEncoder()

/**
 * A chat stream the test drives frame by frame.
 *
 * The response is returned immediately with an open stream, so a test can prove
 * that text appears *before* the turn completes rather than only asserting the
 * final DOM.
 */
function openStream(): StreamHandle {
  const handle: StreamHandle = { controller: null }
  server.use(
    http.post('*/api/v1/chat/stream', () => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          handle.controller = controller
        },
      })
      return new HttpResponse(stream, {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }),
  )
  return handle
}

async function sendQuestion(handle: StreamHandle, question: string): Promise<void> {
  const user = userEvent.setup()
  renderWithProviders(<ChatPage />, { route: '/chat' })
  await user.type(screen.getByLabelText('Ask a question about your course material'), question)
  await user.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(handle.controller).not.toBeNull())
}

async function push(handle: StreamHandle, payload: string): Promise<void> {
  await act(async () => {
    handle.controller?.enqueue(encoder.encode(payload))
  })
}

async function finish(handle: StreamHandle): Promise<void> {
  await act(async () => {
    handle.controller?.close()
  })
}

const CITATION = {
  citation_id: 'cit-1',
  chunk_id: '88888888-8888-4888-8888-888888888888',
  document_id: '99999999-9999-4999-8999-999999999999',
  filename: 'lecture-01.pdf',
  page: 3,
  source_type: 'lecture',
}

describe('chat streaming', () => {
  it('renders streamed tokens incrementally, before the turn completes', async () => {
    const handle = openStream()
    await sendQuestion(handle, 'What is an eigenvalue?')

    await push(handle, sseChunk({ event: 'token', data: JSON.stringify({ text: 'An eigen' }) }))
    expect(await screen.findByText('An eigen')).toBeInTheDocument()
    // The turn is still open: the composer offers Stop rather than Send.
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()

    await push(handle, sseChunk({ event: 'token', data: JSON.stringify({ text: 'value scales.' }) }))
    expect(await screen.findByText('An eigenvalue scales.')).toBeInTheDocument()

    await finish(handle)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    })
  })

  it('renders citations as chips that expand to the source', async () => {
    const handle = openStream()
    await sendQuestion(handle, 'Where is this defined?')

    await push(
      handle,
      sseChunk(
        { event: 'token', data: JSON.stringify({ text: 'See the lecture.' }) },
        { event: 'citations', data: JSON.stringify([CITATION]) },
        { event: 'done', data: JSON.stringify({ proposed_actions: [] }) },
      ),
    )
    await finish(handle)

    const chip = await screen.findByRole('button', { name: /lecture-01\.pdf, p\.3/ })
    expect(chip).toHaveAttribute('aria-expanded', 'false')

    await userEvent.setup().click(chip)

    expect(chip).toHaveAttribute('aria-expanded', 'true')
    const panel = screen.getByRole('region', { name: /Source lecture-01\.pdf/ })
    expect(within(panel).getByText('lecture-01.pdf')).toBeInTheDocument()
    expect(within(panel).getByText('3')).toBeInTheDocument()
    expect(within(panel).getByText('Lecture')).toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Open source document' })).toHaveAttribute(
      'href',
      `/documents/${CITATION.document_id}`,
    )
  })

  it('renders a failed stream as an error state, never as an answer', async () => {
    const handle = openStream()
    await sendQuestion(handle, 'Break please')

    await push(
      handle,
      sseChunk({
        event: 'error',
        data: JSON.stringify({ detail: 'The answer stream failed before it completed.' }),
      }),
    )
    await finish(handle)

    expect(await screen.findByText(/This answer failed/)).toBeInTheDocument()
    expect(
      screen.getByText('The answer stream failed before it completed.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    // A failed turn must not render as a normal answer.
    expect(screen.queryByText(/grounded in your material/)).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Retry' }).length).toBeGreaterThan(0)
  })

  it('shows the degraded banner when the API reports a weakened answer', async () => {
    server.use(
      http.get('*/api/v1/chat/conversations', () =>
        HttpResponse.json([
          {
            id: 'conv-1',
            title: 'Eigenvalues',
            course_id: null,
            created_at: '2026-01-05T00:00:00Z',
            updated_at: '2026-01-05T00:00:00Z',
          },
        ]),
      ),
      http.get('*/api/v1/chat/conversations/conv-1', () =>
        HttpResponse.json(
          makeConversationDetail({
            id: 'conv-1',
            messages: [
              {
                id: 'm-1',
                role: 'assistant',
                content: 'An eigenvalue scales its eigenvector.',
                citations: [],
                degraded: ['reranker_unavailable'],
                grounded: true,
                created_at: '2026-01-05T00:00:00Z',
              },
            ],
          }),
        ),
      ),
    )

    const handle = openStream()
    await sendQuestion(handle, 'Why is the answer weaker?')
    await push(
      handle,
      sseChunk(
        { event: 'token', data: JSON.stringify({ text: 'An eigenvalue scales.' }) },
        { event: 'done', data: JSON.stringify({ proposed_actions: [] }) },
      ),
    )
    await finish(handle)

    expect(await screen.findByText('reranker_unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Answer quality is reduced/)).toBeInTheDocument()
  })

  it('offers a cancel control while streaming', async () => {
    const handle = openStream()
    await sendQuestion(handle, 'Long answer please')
    await push(handle, sseChunk({ event: 'token', data: JSON.stringify({ text: 'Partial' }) }))

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Stop' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    })
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText(/cancelled/)).toBeInTheDocument()
  })
})
