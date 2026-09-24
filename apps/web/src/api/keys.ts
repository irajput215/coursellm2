/**
 * Centralised, typed query keys.
 *
 * Every cache entry in the application is addressed from this object, so
 * invalidating "all roadmaps" after an adapt is a single expression rather than
 * a stringly-typed guess repeated in five components.
 */
export const queryKeys = {
  auth: {
    me: ['auth', 'me'] as const,
  },
  courses: {
    all: ['courses'] as const,
    list: (filters: { courseId?: string | undefined } = {}) =>
      ['courses', 'list', filters] as const,
    detail: (courseId: string) => ['courses', 'detail', courseId] as const,
  },
  documents: {
    all: ['documents'] as const,
    list: (filters: { courseId?: string | undefined; status?: string | undefined } = {}) =>
      ['documents', 'list', filters] as const,
    detail: (documentId: string) => ['documents', 'detail', documentId] as const,
  },
  chat: {
    conversations: ['chat', 'conversations'] as const,
    conversation: (conversationId: string) => ['chat', 'conversation', conversationId] as const,
  },
  roadmaps: {
    all: ['roadmaps'] as const,
    list: (filters: { courseId?: string | undefined } = {}) =>
      ['roadmaps', 'list', filters] as const,
    detail: (roadmapId: string) => ['roadmaps', 'detail', roadmapId] as const,
  },
  progress: {
    summary: (filters: { courseId?: string | undefined } = {}) =>
      ['progress', 'summary', filters] as const,
    overview: (filters: { courseId?: string | undefined } = {}) =>
      ['progress', 'overview', filters] as const,
    attempts: (page: { limit: number; offset: number }) =>
      ['progress', 'attempts', page] as const,
  },
  quizzes: {
    detail: (quizId: string) => ['quizzes', 'detail', quizId] as const,
  },
  recommendations: {
    list: (filters: { courseId?: string | undefined; difficultyMax?: number | undefined } = {}) =>
      ['recommendations', 'list', filters] as const,
    resources: (filters: {
      query?: string | undefined
      resourceType?: string | undefined
      trust?: string | undefined
      limit: number
      offset: number
    }) => ['recommendations', 'resources', filters] as const,
    resource: (resourceId: string) => ['recommendations', 'resource', resourceId] as const,
  },
} as const
