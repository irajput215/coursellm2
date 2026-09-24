/**
 * Every server interaction the UI performs, as a TanStack Query hook.
 *
 * There is no `useEffect`-plus-`useState` fetching anywhere in this application:
 * loading, error and empty states all come from the query result, and cache
 * invalidation happens here rather than in the components that trigger a write.
 */
import { useMutation, useQuery, useQueryClient, type Query } from '@tanstack/react-query'

import { fetchMe } from './auth'
import { apiRequest } from './client'
import { queryKeys } from './keys'
import type {
  AnswerSubmitRequest,
  AssessmentResult,
  AttemptPage,
  CataloguePage,
  ChatResponse,
  ConfirmProposalResponse,
  ConversationDetail,
  ConversationSummary,
  Course,
  CourseCreateRequest,
  CourseDetail,
  DocumentDetail,
  DocumentRecord,
  DocumentStatus,
  ProgressOverview,
  ProgressSummary,
  Quiz,
  QuizCreateRequest,
  RecommendationList,
  ResourceWithConcepts,
  Roadmap,
  RoadmapAdaptRequest,
  RoadmapCreateRequest,
  RoadmapDetail,
  RoadmapStep,
  RoadmapStepUpdateRequest,
  User,
} from './types'
import { uploadDocument, type UploadDocumentInput } from './upload'

type QueryParams = Record<string, string | number | boolean | undefined | null>

/**
 * Poll interval, as TanStack Query accepts it.
 *
 * A function form is used so a page can decide whether to keep polling from the
 * data it already has (for example, while a document is still ingesting)
 * without mirroring that data into its own `useState` + `useEffect`.
 */
type RefetchInterval<TData> =
  | number
  | false
  | ((query: Query<TData, Error>) => number | false | undefined)

function withQuery(path: string, params: QueryParams): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const query = search.toString()
  return query === '' ? path : `${path}?${query}`
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.auth.me,
    queryFn: fetchMe,
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// Courses
// ---------------------------------------------------------------------------

export function useCourses() {
  return useQuery({
    queryKey: queryKeys.courses.list(),
    queryFn: () => apiRequest<Course[]>('/courses'),
  })
}

export function useCourse(courseId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.courses.detail(courseId ?? ''),
    queryFn: () => apiRequest<CourseDetail>(`/courses/${courseId ?? ''}`),
    enabled: courseId !== undefined && courseId !== '',
  })
}

export function useCreateCourse() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: CourseCreateRequest) =>
      apiRequest<Course>('/courses', { method: 'POST', body: payload }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.courses.all })
    },
  })
}

export function useDeleteCourse() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (courseId: string) =>
      apiRequest<void>(`/courses/${courseId}`, { method: 'DELETE' }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.courses.all })
      void client.invalidateQueries({ queryKey: queryKeys.documents.all })
    },
  })
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

export function useDocuments(
  filters: {
    courseId?: string | undefined
    status?: DocumentStatus | undefined
  },
  options: { refetchInterval?: RefetchInterval<DocumentRecord[]> } = {},
) {
  return useQuery({
    queryKey: queryKeys.documents.list(filters),
    queryFn: () =>
      apiRequest<DocumentRecord[]>(
        withQuery('/documents', { course_id: filters.courseId, status: filters.status }),
      ),
    ...(options.refetchInterval === undefined
      ? {}
      : { refetchInterval: options.refetchInterval }),
  })
}

export function useDocument(
  documentId: string | undefined,
  options: { refetchInterval?: RefetchInterval<DocumentDetail> } = {},
) {
  return useQuery({
    queryKey: queryKeys.documents.detail(documentId ?? ''),
    queryFn: () => apiRequest<DocumentDetail>(`/documents/${documentId ?? ''}`),
    enabled: documentId !== undefined && documentId !== '',
    ...(options.refetchInterval === undefined
      ? {}
      : { refetchInterval: options.refetchInterval }),
  })
}

export function useUploadDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: UploadDocumentInput) => uploadDocument(input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.documents.all })
      void client.invalidateQueries({ queryKey: queryKeys.courses.all })
    },
  })
}

export function useDeleteDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) =>
      apiRequest<void>(`/documents/${documentId}`, { method: 'DELETE' }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.documents.all })
      void client.invalidateQueries({ queryKey: queryKeys.courses.all })
    },
  })
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export function useConversations() {
  return useQuery({
    queryKey: queryKeys.chat.conversations,
    queryFn: () => apiRequest<ConversationSummary[]>('/chat/conversations'),
  })
}

export function useConversation(conversationId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.chat.conversation(conversationId ?? ''),
    queryFn: () =>
      apiRequest<ConversationDetail>(`/chat/conversations/${conversationId ?? ''}`),
    enabled: conversationId !== undefined && conversationId !== '',
  })
}

/** Execute a withheld write after the student explicitly confirms it. */
export function useConfirmProposal() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (token: string) =>
      apiRequest<ConfirmProposalResponse>('/chat/confirm', {
        method: 'POST',
        body: { token },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.progress.summary() })
      void client.invalidateQueries({ queryKey: queryKeys.roadmaps.all })
      void client.invalidateQueries({ queryKey: queryKeys.courses.all })
    },
  })
}

/** The non-streaming chat call. Used for retry-on-failure of a whole turn. */
export function useAskChat() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { question: string; conversationId?: string | undefined }) =>
      apiRequest<ChatResponse>('/chat', {
        method: 'POST',
        body: {
          question: body.question,
          ...(body.conversationId === undefined ? {} : { conversation_id: body.conversationId }),
        },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.chat.conversations })
    },
  })
}

// ---------------------------------------------------------------------------
// Roadmaps
// ---------------------------------------------------------------------------

export function useRoadmaps(courseId?: string | undefined) {
  return useQuery({
    queryKey: queryKeys.roadmaps.list({ courseId }),
    queryFn: () => apiRequest<Roadmap[]>(withQuery('/roadmaps', { course_id: courseId })),
  })
}

export function useRoadmap(roadmapId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.roadmaps.detail(roadmapId ?? ''),
    queryFn: () => apiRequest<RoadmapDetail>(`/roadmaps/${roadmapId ?? ''}`),
    enabled: roadmapId !== undefined && roadmapId !== '',
  })
}

export function useCreateRoadmap() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: RoadmapCreateRequest) =>
      apiRequest<RoadmapDetail>('/roadmaps', { method: 'POST', body: payload }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.roadmaps.all })
      void client.invalidateQueries({ queryKey: queryKeys.progress.summary() })
    },
  })
}

export function useAdaptRoadmap() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ roadmapId, payload }: { roadmapId: string; payload: RoadmapAdaptRequest }) =>
      apiRequest<RoadmapDetail>(`/roadmaps/${roadmapId}/adapt`, {
        method: 'POST',
        body: payload,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.roadmaps.all })
      void client.invalidateQueries({ queryKey: queryKeys.progress.summary() })
    },
  })
}

export function useUpdateRoadmapStep() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      roadmapId,
      stepId,
      payload,
    }: {
      roadmapId: string
      stepId: string
      payload: RoadmapStepUpdateRequest
    }) =>
      apiRequest<RoadmapStep>(`/roadmaps/${roadmapId}/steps/${stepId}`, {
        method: 'PATCH',
        body: payload,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.roadmaps.all })
      void client.invalidateQueries({ queryKey: queryKeys.progress.summary() })
    },
  })
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

export function useProgressSummary(courseId?: string | undefined) {
  return useQuery({
    queryKey: queryKeys.progress.summary({ courseId }),
    queryFn: () =>
      apiRequest<ProgressSummary>(withQuery('/progress/summary', { course_id: courseId })),
  })
}

export function useProgressOverview(courseId?: string | undefined) {
  return useQuery({
    queryKey: queryKeys.progress.overview({ courseId }),
    queryFn: () => apiRequest<ProgressOverview>(withQuery('/progress', { course_id: courseId })),
  })
}

export function useAttempts(page: { limit: number; offset: number }) {
  return useQuery({
    queryKey: queryKeys.progress.attempts(page),
    queryFn: () =>
      apiRequest<AttemptPage>(
        withQuery('/progress/attempts', { limit: page.limit, offset: page.offset }),
      ),
  })
}

// ---------------------------------------------------------------------------
// Quizzes
// ---------------------------------------------------------------------------

export function useQuiz(quizId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.quizzes.detail(quizId ?? ''),
    queryFn: () => apiRequest<Quiz>(`/quizzes/${quizId ?? ''}`),
    enabled: quizId !== undefined && quizId !== '',
  })
}

export function useCreateQuiz() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: QuizCreateRequest) =>
      apiRequest<Quiz>('/quizzes', { method: 'POST', body: payload }),
    onSuccess: (quiz) => {
      client.setQueryData(queryKeys.quizzes.detail(quiz.quiz_id), quiz)
    },
  })
}

export function useSubmitAnswer() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      quizId,
      itemId,
      answer,
    }: {
      quizId: string
      itemId: string
      answer: AnswerSubmitRequest
    }) =>
      apiRequest<AssessmentResult>(`/quizzes/${quizId}/items/${itemId}/answer`, {
        method: 'POST',
        body: answer,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.progress.summary() })
      void client.invalidateQueries({ queryKey: queryKeys.progress.overview() })
      void client.invalidateQueries({ queryKey: ['progress', 'attempts'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Recommendations
// ---------------------------------------------------------------------------

export function useRecommendations(filters: {
  courseId?: string | undefined
  difficultyMax?: number | undefined
}) {
  return useQuery({
    queryKey: queryKeys.recommendations.list(filters),
    queryFn: () =>
      apiRequest<RecommendationList>(
        withQuery('/recommendations', {
          course_id: filters.courseId,
          difficulty_max: filters.difficultyMax,
        }),
      ),
  })
}

export function useResources(filters: {
  query?: string | undefined
  resourceType?: string | undefined
  trust?: string | undefined
  limit?: number
  offset?: number
}) {
  const limit = filters.limit ?? 20
  const offset = filters.offset ?? 0
  return useQuery({
    queryKey: queryKeys.recommendations.resources({
      query: filters.query,
      resourceType: filters.resourceType,
      trust: filters.trust,
      limit,
      offset,
    }),
    queryFn: () =>
      apiRequest<CataloguePage>(
        withQuery('/recommendations/resources', {
          q: filters.query,
          resource_type: filters.resourceType,
          trust: filters.trust,
          limit,
          offset,
        }),
      ),
  })
}

export function useResource(resourceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.recommendations.resource(resourceId ?? ''),
    queryFn: () =>
      apiRequest<ResourceWithConcepts>(`/recommendations/${resourceId ?? ''}`),
    enabled: resourceId !== undefined && resourceId !== '',
  })
}

// ---------------------------------------------------------------------------
// Convenience re-exports so pages import hooks from one module
// ---------------------------------------------------------------------------

export type { UploadDocumentInput }
export { fetchMe }
export type { User }
