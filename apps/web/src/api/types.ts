/**
 * Named aliases for the generated schema.
 *
 * This file is the *only* place response shapes are named. Pages import these
 * aliases and never redeclare a `Course` or a `Submission`-style local type, so
 * every consumer is guaranteed to agree on what a field is. Each alias is
 * derived from `src/api/schema.d.ts`, which is generated from the live OpenAPI
 * document (`npm run schema:generate`).
 */
import type { components } from './schema'

type Schemas = components['schemas']

// Auth ----------------------------------------------------------------------
export type User = Schemas['UserResponse']
export type UserRole = Schemas['UserRole']
export type TokenResponse = Schemas['TokenResponse']
export type LoginRequest = Schemas['LoginRequest']
export type RegisterRequest = Schemas['RegisterRequest']
export type RegistrationResponse = Schemas['RegistrationResponse']
export type LogoutRequest = Schemas['LogoutRequest']

// Courses -------------------------------------------------------------------
export type Course = Schemas['CourseResponse']
export type CourseDetail = Schemas['CourseDetailResponse']
export type CourseCreateRequest = Schemas['CourseCreateRequest']

// Documents -----------------------------------------------------------------
export type DocumentSummary = Schemas['DocumentSummary']
export type DocumentRecord = Schemas['DocumentResponse']
export type DocumentDetail = Schemas['DocumentDetailResponse']
export type DocumentIngestion = Schemas['DocumentIngestionResponse']
export type DocumentStatus = Schemas['DocumentStatus']
export type SourceType = Schemas['SourceType']

// Chat ----------------------------------------------------------------------
export type ChatRequest = Schemas['ChatRequest']
export type ChatResponse = Schemas['ChatResponse']
export type ChatEngine = Schemas['ChatRequest']['engine']
export type Citation = Schemas['CitationResponse']
export type QuizCitation = Schemas['QuizCitationResponse']
export type ProposedAction = Schemas['ProposedAction']
export type ConfirmProposalRequest = Schemas['ConfirmProposalRequest']
export type ConfirmProposalResponse = Schemas['ConfirmProposalResponse']
export type ConversationSummary = Schemas['ConversationSummary']
export type ConversationDetail = Schemas['ConversationDetailResponse']
export type Message = Schemas['MessageResponse']
export type MessageRole = Schemas['MessageRole']

/** Any citation the API can return. Both shapes carry the same open-in-source fields. */
export type AnyCitation = Citation | QuizCitation

// Roadmaps ------------------------------------------------------------------
export type Roadmap = Schemas['RoadmapResponse']
export type RoadmapDetail = Schemas['RoadmapDetailResponse']
export type RoadmapStep = Schemas['RoadmapStepResponse']
export type RoadmapStepStatus = Schemas['RoadmapStepStatus']
export type RoadmapStatus = Schemas['RoadmapStatus']
export type RoadmapCreateRequest = Schemas['RoadmapCreateRequest']
export type RoadmapAdaptRequest = Schemas['RoadmapAdaptRequest']
export type RoadmapStepUpdateRequest = Schemas['RoadmapStepUpdateRequest']
export type RoadmapPosition = Schemas['RoadmapPositionResponse']

// Progress ------------------------------------------------------------------
export type ProgressSummary = Schemas['ProgressSummaryResponse']
export type ProgressOverview = Schemas['ProgressOverviewResponse']
export type NextAction = Schemas['NextActionResponse']
export type Attempt = Schemas['AttemptResponse']
export type AttemptPage = Schemas['AttemptPageResponse']

// Quizzes -------------------------------------------------------------------
export type Quiz = Schemas['QuizResponse']
export type QuizItem = Schemas['QuizItemResponse']
export type QuizItemType = Schemas['QuizItemResponse']['item_type']
export type QuizCreateRequest = Schemas['QuizCreateRequest']
export type AnswerSubmitRequest = Schemas['AnswerSubmitRequest']
export type AssessmentResult = Schemas['AssessmentResultResponse']
export type RubricCriterion = Schemas['RubricCriterionResponse']
export type RubricScore = Schemas['RubricScoreResponse']
export type Misconception = Schemas['MisconceptionResponse']

// Recommendations -----------------------------------------------------------
export type RecommendationList = Schemas['RecommendationListResponse']
export type RecommendationItem = Schemas['RecommendationItemResponse']
export type RecommendationGap = Schemas['RecommendationGapResponse']
export type RecommendationExplanation = Schemas['RecommendationExplanation']
export type Resource = Schemas['ResourceResponse']
export type ResourceWithConcepts = Schemas['ResourceWithConceptsResponse']
export type CataloguePage = Schemas['CataloguePageResponse']
export type ResourceType = Schemas['ResourceType']
export type SourceTrust = Schemas['SourceTrust']

// Health --------------------------------------------------------------------
export type Health = Schemas['HealthResponse']
export type Readiness = Schemas['ReadinessResponse']
export type CheckResult = Schemas['CheckResult']
