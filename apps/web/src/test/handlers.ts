import { HttpResponse, http } from 'msw'

import type {
  AttemptPage,
  ConversationDetail,
  Course,
  DocumentRecord,
  ProgressSummary,
  Quiz,
  RecommendationList,
  RoadmapDetail,
  RoadmapStep,
  User,
} from '@/api/types'

export const TEST_ORIGIN = 'http://localhost:3000'

export const TEST_USER: User = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'student@example.edu',
  full_name: 'Test Student',
  role: 'member',
  tenant_id: '22222222-2222-4222-8222-222222222222',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

export const TEST_TOKENS = {
  access_token: 'access-token-value',
  refresh_token: 'refresh-token-value-1234',
  token_type: 'bearer',
  expires_in: 900,
}

export function errorEnvelope(
  status: number,
  body: { error: string; detail: string; request_id?: string | null },
) {
  return HttpResponse.json(
    { error: body.error, detail: body.detail, request_id: body.request_id ?? null },
    { status },
  )
}

export const EMPTY_PROGRESS_SUMMARY: ProgressSummary = {
  mastery: {},
  weak_concepts: [],
  stale_concepts: [],
  velocity: null,
  attempt_counts: {},
  recent_attempts: [],
  current_position: null,
  next_action: null,
  degraded: [],
}

export const EMPTY_ATTEMPTS: AttemptPage = { items: [], total: 0, limit: 20, offset: 0 }

export const EMPTY_RECOMMENDATIONS: RecommendationList = {
  recommendations: [],
  gaps: [],
  degraded: [],
  personalised: false,
}

export function makeCourse(overrides: Partial<Course> = {}): Course {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    name: 'Linear Algebra',
    code: 'MATH-221',
    description: 'Vector spaces and linear maps.',
    created_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

export function makeDocument(overrides: Partial<DocumentRecord> = {}): DocumentRecord {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    course_id: '33333333-3333-4333-8333-333333333333',
    filename: 'lecture-01.pdf',
    content_type: 'application/pdf',
    size_bytes: 2048,
    sha256: 'a'.repeat(64),
    status: 'ready',
    source_type: 'lecture',
    page_count: 12,
    quarantine_state: 'clean',
    injection_score: 0,
    error_message: null,
    created_at: '2026-01-03T00:00:00Z',
    ...overrides,
  }
}

export function makeStep(overrides: Partial<RoadmapStep> = {}): RoadmapStep {
  return {
    id: 'step-1',
    concept_id: 'c-1',
    order_index: 0,
    title: 'Vectors',
    description: 'Understand vector spaces.',
    status: 'available',
    blocked_by: [],
    estimated_hours: 2,
    completed_at: null,
    ...overrides,
  }
}

export function makeRoadmap(steps: RoadmapStep[]): RoadmapDetail {
  return {
    id: '55555555-5555-4555-8555-555555555555',
    course_id: null,
    goal_concept_id: null,
    goal_text: 'Understand eigenvalues',
    revision: 1,
    status: 'active',
    estimated_hours: 10,
    reason: 'Built from your goal and current mastery.',
    created_at: '2026-01-04T00:00:00Z',
    steps,
  }
}

export function makeQuiz(overrides: Partial<Quiz> = {}): Quiz {
  return {
    quiz_id: '66666666-6666-4666-8666-666666666666',
    course_id: '33333333-3333-4333-8333-333333333333',
    concept_ids: [],
    difficulty: 'medium',
    item_types: ['multiple_choice'],
    items: [],
    citations: [],
    requested_items: 1,
    shortfall: 0,
    degraded: [],
    ...overrides,
  }
}

export function makeConversationDetail(
  overrides: Partial<ConversationDetail> = {},
): ConversationDetail {
  return {
    id: '77777777-7777-4777-8777-777777777777',
    title: 'Eigenvalues',
    course_id: null,
    created_at: '2026-01-05T00:00:00Z',
    messages: [],
    ...overrides,
  }
}

/** Encodes SSE frames the way `sse-starlette` does (CRLF separators). */
export function sseChunk(...frames: { event: string; data: string }[]): string {
  return frames
    .map((frame) => `event: ${frame.event}\r\ndata: ${frame.data}\r\n\r\n`)
    .join('')
}

export const handlers = [
  http.post('*/api/v1/auth/token', () => HttpResponse.json(TEST_TOKENS)),
  http.post('*/api/v1/auth/refresh', () => HttpResponse.json(TEST_TOKENS)),
  http.post('*/api/v1/auth/logout', () => new HttpResponse(null, { status: 204 })),
  http.get('*/api/v1/auth/me', () => HttpResponse.json(TEST_USER)),

  http.get('*/api/v1/courses', () => HttpResponse.json([])),
  http.post('*/api/v1/courses', () => HttpResponse.json(makeCourse(), { status: 201 })),
  http.get('*/api/v1/courses/:courseId', () =>
    errorEnvelope(404, { error: 'not_found', detail: 'Course not found.', request_id: 'req-course' }),
  ),

  http.get('*/api/v1/documents', () => HttpResponse.json([])),
  http.get('*/api/v1/documents/:documentId', () =>
    errorEnvelope(404, {
      error: 'not_found',
      detail: 'Document not found.',
      request_id: 'req-doc',
    }),
  ),

  http.get('*/api/v1/chat/conversations', () => HttpResponse.json([])),
  http.get('*/api/v1/chat/conversations/:conversationId', () =>
    errorEnvelope(404, {
      error: 'not_found',
      detail: 'Conversation not found.',
      request_id: 'req-conv',
    }),
  ),

  http.get('*/api/v1/roadmaps', () => HttpResponse.json([])),
  http.get('*/api/v1/roadmaps/:roadmapId', () =>
    errorEnvelope(404, {
      error: 'not_found',
      detail: 'Roadmap not found.',
      request_id: 'req-roadmap',
    }),
  ),

  http.get('*/api/v1/progress/summary', () => HttpResponse.json(EMPTY_PROGRESS_SUMMARY)),
  http.get('*/api/v1/progress/attempts', () => HttpResponse.json(EMPTY_ATTEMPTS)),
  http.get('*/api/v1/progress', () =>
    HttpResponse.json({
      mastery: {},
      weak_concepts: [],
      stale_concepts: [],
      velocity: null,
      attempt_counts: {},
      current_position: null,
      next_action: null,
      degraded: [],
    }),
  ),

  http.get('*/api/v1/quizzes/:quizId', () =>
    errorEnvelope(404, { error: 'not_found', detail: 'Quiz not found.', request_id: 'req-quiz' }),
  ),

  http.get('*/api/v1/recommendations', () => HttpResponse.json(EMPTY_RECOMMENDATIONS)),
  http.get('*/api/v1/recommendations/resources', () =>
    HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
  ),
]
