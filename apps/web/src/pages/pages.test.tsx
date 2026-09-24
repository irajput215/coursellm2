import { HttpResponse, http } from 'msw'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { CoursesPage } from '@/pages/CoursesPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { DocumentsPage } from '@/pages/DocumentsPage'
import { ProgressPage } from '@/pages/ProgressPage'
import { QuizPage } from '@/pages/QuizPage'
import { RecommendationsPage } from '@/pages/RecommendationsPage'
import { RoadmapPage } from '@/pages/RoadmapPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { errorEnvelope, makeCourse, makeQuiz, makeStep, makeRoadmap, TEST_USER } from '@/test/handlers'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'
import { AuthContext, type AuthContextValue } from '@/features/auth/context'

/**
 * Settings reads the session, so it is rendered with an explicit auth context
 * rather than going through the whole provider (whose bootstrap would add a
 * second network round trip to every state test).
 */
const AUTH_VALUE: AuthContextValue = {
  status: 'authenticated',
  user: TEST_USER,
  isAuthenticated: true,
  login: async () => undefined,
  register: async () => undefined,
  signOut: async () => undefined,
  retryBootstrap: () => undefined,
}

function withAuth(element: ReactElement): ReactElement {
  return <AuthContext.Provider value={AUTH_VALUE}>{element}</AuthContext.Provider>
}

function neverResolves(): Promise<never> {
  return new Promise<never>(() => {
    /* intentionally pending for the whole test */
  })
}

async function expectLoading(path: string, element: ReactElement, route = '/'): Promise<void> {
  server.use(http.get(path, neverResolves))
  renderWithProviders(element, { route })
  expect(await screen.findByLabelText('Loading content')).toBeInTheDocument()
}

async function expectError(path: string, element: ReactElement, route = '/'): Promise<void> {
  server.use(
    http.get(path, () =>
      errorEnvelope(500, {
        error: 'internal_error',
        detail: 'Boom from the API.',
        request_id: 'req-boom',
      }),
    ),
  )
  renderWithProviders(element, { route })
  expect(await screen.findByText('Boom from the API.')).toBeInTheDocument()
  expect(screen.getByText('req-boom')).toBeInTheDocument()
}

describe('page states', () => {
  it('dashboard renders its loading state', async () => {
    await expectLoading('*/api/v1/progress/summary', <DashboardPage />)
  })

  it('dashboard renders its empty state when the API has nothing to report', async () => {
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText('Nothing to summarise yet')).toBeInTheDocument()
  })

  it('dashboard renders its error state with the request id', async () => {
    await expectError('*/api/v1/progress/summary', <DashboardPage />)
  })

  it('courses renders loading, empty and error states', async () => {
    await expectLoading('*/api/v1/courses', <CoursesPage />)
  })

  it('courses renders its empty state', async () => {
    renderWithProviders(<CoursesPage />)
    expect(await screen.findByText('No courses yet')).toBeInTheDocument()
  })

  it('courses renders its error state', async () => {
    await expectError('*/api/v1/courses', <CoursesPage />)
  })

  it('documents renders loading, empty and error states', async () => {
    await expectLoading('*/api/v1/documents', <DocumentsPage />)
  })

  it('documents renders its empty state', async () => {
    renderWithProviders(<DocumentsPage />)
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
  })

  it('documents renders its error state', async () => {
    await expectError('*/api/v1/documents', <DocumentsPage />)
  })

  it('roadmap renders loading, empty and error states', async () => {
    await expectLoading('*/api/v1/roadmaps', <RoadmapPage />)
  })

  it('roadmap renders its empty state', async () => {
    renderWithProviders(<RoadmapPage />)
    expect(await screen.findByText('No roadmaps yet')).toBeInTheDocument()
  })

  it('roadmap renders its error state', async () => {
    await expectError('*/api/v1/roadmaps', <RoadmapPage />)
  })

  it('recommendations renders loading, empty and error states', async () => {
    await expectLoading('*/api/v1/recommendations', <RecommendationsPage />)
  })

  it('recommendations renders its empty state', async () => {
    renderWithProviders(<RecommendationsPage />)
    expect(await screen.findByText('No recommendations right now')).toBeInTheDocument()
  })

  it('recommendations renders its error state', async () => {
    await expectError('*/api/v1/recommendations', <RecommendationsPage />)
  })

  it('progress renders loading, empty and error states', async () => {
    await expectLoading('*/api/v1/progress/summary', <ProgressPage />)
  })

  it('progress renders its empty state', async () => {
    renderWithProviders(<ProgressPage />)
    expect(await screen.findByText('No mastery recorded')).toBeInTheDocument()
    expect(await screen.findByText('No attempts yet')).toBeInTheDocument()
  })

  it('progress renders its error state', async () => {
    await expectError('*/api/v1/progress/summary', <ProgressPage />)
  })

  it('quiz renders loading, empty and error states for a loaded quiz', async () => {
    await expectLoading('*/api/v1/quizzes/q-1', <QuizPage />, '/quiz?quiz=q-1')
  })

  it('quiz renders its empty state when the generator produced no items', async () => {
    server.use(
      http.get('*/api/v1/quizzes/q-1', () => HttpResponse.json(makeQuiz({ quiz_id: 'q-1' }))),
    )
    renderWithProviders(<QuizPage />, { route: '/quiz?quiz=q-1' })
    expect(await screen.findByText('The generator produced no items')).toBeInTheDocument()
  })

  it('quiz renders its error state', async () => {
    await expectError('*/api/v1/quizzes/q-1', <QuizPage />, '/quiz?quiz=q-1')
  })

  it('settings renders its loading and error states', async () => {
    await expectLoading('*/api/v1/auth/me', withAuth(<SettingsPage />))
  })

  it('settings renders an error state when the profile cannot be read', async () => {
    await expectError('*/api/v1/auth/me', withAuth(<SettingsPage />))
  })
})

describe('dashboard content', () => {
  it('shows only values the progress API returned', async () => {
    server.use(
      http.get('*/api/v1/courses', () => HttpResponse.json([makeCourse()])),
      http.get('*/api/v1/progress/summary', () =>
        HttpResponse.json({
          mastery: { 'c-1': 0.5, 'c-2': 0.25 },
          weak_concepts: ['c-1'],
          stale_concepts: ['c-2'],
          velocity: 1.5,
          attempt_counts: { 'c-1': 3 },
          recent_attempts: [
            {
              id: 'attempt-1',
              quiz_id: 'quiz-1',
              course_id: 'course-1',
              item_id: 'item-1',
              concept_ids: ['c-1'],
              answer: 'because',
              score: 0.75,
              rubric: [],
              misconceptions: ['confuses basis with span'],
              created_at: '2026-02-01T10:00:00Z',
            },
          ],
          current_position: null,
          next_action: {
            kind: 'review',
            title: 'Review eigenvalues',
            rationale: 'Your last attempt on eigenvalues scored below your average.',
            roadmap_id: null,
            step_id: null,
            concept_id: null,
          },
          degraded: [],
        }),
      ),
    )

    const { container } = renderWithProviders(<DashboardPage />)

    expect(await screen.findByText('Review eigenvalues')).toBeInTheDocument()
    expect(screen.getByText('Concepts tracked').parentElement).toHaveTextContent('2')
    expect(screen.getByText('Attempts logged').parentElement).toHaveTextContent('3')
    expect(screen.getByText('Weak concepts').parentElement).toHaveTextContent('1')
    expect(screen.getByText('Stale concepts').parentElement).toHaveTextContent('1')
    expect(screen.getByText('Velocity').parentElement).toHaveTextContent('1.50')
    expect(screen.getByText('Review eigenvalues')).toBeInTheDocument()

    // The prototype's hardcoded strings must never appear.
    const text = container.textContent ?? ''
    expect(text).not.toContain('Questions Asked')
    expect(text).not.toContain('Avg Evaluation Score')
    expect(text).not.toContain('94%')
    expect(text).not.toContain('142')
  })

  it('renders a real empty state rather than invented numbers', async () => {
    const { container } = renderWithProviders(<DashboardPage />)
    expect(await screen.findByText('Nothing to summarise yet')).toBeInTheDocument()
    const text = container.textContent ?? ''
    expect(text).not.toContain('Questions Asked')
    expect(text).not.toContain('142')
    expect(text).not.toContain('94%')
  })
})

describe('roadmap content', () => {
  it('renders the steps the API returned', async () => {
    server.use(
      http.get('*/api/v1/roadmaps', () =>
        HttpResponse.json([
          {
            id: 'r-1',
            course_id: null,
            goal_concept_id: null,
            goal_text: 'Understand eigenvalues',
            revision: 2,
            status: 'active',
            estimated_hours: 6,
            reason: 'Adapted after your last quiz.',
            created_at: '2026-02-01T10:00:00Z',
          },
        ]),
      ),
      http.get('*/api/v1/roadmaps/r-1', () =>
        HttpResponse.json({
          ...makeRoadmap([
            makeStep({ id: 's-1', concept_id: 'c-1', order_index: 0, title: 'Basis vectors', status: 'completed' }),
            makeStep({ id: 's-2', concept_id: 'c-2', order_index: 1, title: 'Eigenvalues', status: 'blocked', blocked_by: ['c-1'] }),
          ]),
          reason: 'Adapted after your last quiz.',
        }),
      ),
    )

    renderWithProviders(<RoadmapPage />)

    expect(await screen.findByText('Understand eigenvalues')).toBeInTheDocument()
    expect(screen.getByText(/Adapted after your last quiz/)).toBeInTheDocument()
    expect(screen.getByText(/1\. Basis vectors/)).toBeInTheDocument()
    expect(screen.getByText(/2\. Eigenvalues/)).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
  })
})

describe('settings content', () => {
  it('renders the profile the API returned', async () => {
    renderWithProviders(withAuth(<SettingsPage />))
    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument()
    expect(screen.getByText(TEST_USER.id)).toBeInTheDocument()
  })
})
