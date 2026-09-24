/**
 * Generated file — do not edit by hand.
 *
 * Source: the live OpenAPI document of the CourseLLM API.
 * Regenerate with `npm run schema:generate` from apps/web.
 */
export interface paths {
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Liveness probe
         * @description Cheap, dependency-free. Never performs I/O.
         */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Readiness probe
         * @description Runs every registered dependency probe. Returns 503 when a critical one fails.
         */
        get: operations["readyz_readyz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Prometheus metrics
         * @description The process metrics registry in Prometheus text format. No authentication is required and no tenant-scoped label is ever emitted.
         */
        get: operations["metrics_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/register": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create a workspace and its owner
         * @description Creates a tenant and its first user, then returns tokens. Registration always creates a new workspace; joining an existing one is an invitation flow with different authorisation.
         */
        post: operations["register_api_v1_auth_register_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/token": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Exchange credentials for tokens
         * @description Returns an access token and a refresh token. Every failure returns the same error so the endpoint cannot be used to enumerate accounts.
         */
        post: operations["login_api_v1_auth_token_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate a refresh token
         * @description Issues a new token pair and revokes the presented refresh token. Presenting an already-revoked token is treated as a possible theft and refused.
         */
        post: operations["refresh_api_v1_auth_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Revoke a refresh token
         * @description Idempotent: revoking an unknown or already-revoked token also succeeds.
         */
        post: operations["logout_api_v1_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * The authenticated user
         * @description Returns the caller's profile. The response model is defined independently of the database model, so a sensitive column added to the table cannot silently appear here.
         */
        get: operations["me_api_v1_auth_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List the caller's courses */
        get: operations["list_courses_api_v1_courses_get"];
        put?: never;
        /** Create a course */
        post: operations["create_course_api_v1_courses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/courses/{course_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * A course and its documents
         * @description A course belonging to another tenant or another user is reported as not found. Reporting it as forbidden would confirm that it exists.
         */
        get: operations["get_course_api_v1_courses__course_id__get"];
        put?: never;
        post?: never;
        /** Delete a course and everything in it */
        delete: operations["delete_course_api_v1_courses__course_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List the caller's documents
         * @description Newest first, optionally filtered by course and ingestion status.
         */
        get: operations["list_documents_route_api_v1_documents_get"];
        put?: never;
        /**
         * Upload a document into a course
         * @description Accepts a multipart upload, validates the extension, size and leading bytes, stores it under a server-generated key, then parses, chunks, embeds and indexes it inline in the request.
         *
         *     **Course resolution.** Supply `course_id` for a course that already exists, or `course_name` to resolve it by name. A `course_name` the caller does not have yet is **created**, exactly as `POST /courses` does, and its id is returned in `course_id`. A `course_id` belonging to another tenant or another user is a 404 and nothing is stored.
         *
         *     **Deduplication.** Identical bytes in the same course return the existing document with `200` and `reused=true`; pass `reingest=true` to force the pipeline to replace its chunks instead.
         */
        post: operations["upload_document_api_v1_documents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/{document_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * One document
         * @description A document belonging to another tenant or another user is reported as not found. Reporting it as forbidden would confirm that it exists.
         */
        get: operations["get_document_route_api_v1_documents__document_id__get"];
        put?: never;
        post?: never;
        /**
         * Delete a document and its stored object
         * @description Removes the document row, cascading to chunks, embeddings, terms and the lexical statistics, then removes the stored object. If the object cannot be removed the request still succeeds: an orphaned blob is a cleanup job.
         */
        delete: operations["delete_document_route_api_v1_documents__document_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/chat": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ask a grounded question
         * @description Retrieves evidence from the caller's own course material, answers with verified inline citations, and refuses explicitly when the evidence does not support an answer. The turn is persisted either way. ``engine`` selects the bounded agent graph (the default) or the direct retrieval-augmented path; both share the grounding, citation and refusal machinery and produce equivalent answers for a simple factual question.
         */
        post: operations["chat_api_v1_chat_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/chat/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm and execute a proposed action
         * @description Execute a write the agent proposed but did not perform. The body carries only the signed token from the proposal; the server re-validates the signature, expiry, the current tenant, the permission matrix and the tool's argument schema before the handler runs. The model never holds an execution capability for a consequential action.
         */
        post: operations["confirm_proposal_api_v1_chat_confirm_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/chat/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ask a grounded question and stream the answer
         * @description Server-sent events. ``token`` events carry partial text as it is produced; the final ``citations`` event carries the verified citation list; ``done`` ends the stream. An ``error`` event is emitted instead if the stream fails. With ``engine='agent'`` (the default) the graph runs the turn first and its answer is emitted as a single ``token`` event.
         */
        post: operations["stream_chat_api_v1_chat_stream_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/chat/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List the caller's conversations */
        get: operations["list_conversations_api_v1_chat_conversations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/chat/conversations/{conversation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * A conversation and its messages
         * @description A conversation belonging to another tenant or another user is reported as not found. Reporting it as forbidden would confirm that it exists.
         */
        get: operations["get_conversation_api_v1_chat_conversations__conversation_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roadmaps": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List the caller's roadmaps
         * @description The newest revision of each goal the caller has planned.
         */
        get: operations["list_roadmaps_api_v1_roadmaps_get"];
        put?: never;
        /**
         * Create a roadmap from a goal
         * @description Plans the prerequisite closure of the goal, subtracts concepts the student has mastery evidence for, and orders the remainder deterministically. Re-posting an unchanged goal returns the existing revision rather than creating a duplicate.
         */
        post: operations["create_roadmap_api_v1_roadmaps_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roadmaps/{roadmap_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * A roadmap and its ordered steps
         * @description A roadmap belonging to another tenant or another user is reported as not found. Reporting it as forbidden would confirm that it exists.
         */
        get: operations["get_roadmap_api_v1_roadmaps__roadmap_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roadmaps/{roadmap_id}/adapt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Revise a roadmap from measured progress
         * @description Preserves completed steps, drops steps whose concepts are now mastered, re-orders unblocked steps, inserts newly surfaced prerequisites and records why. A no-op adaptation returns the current revision unchanged.
         */
        post: operations["adapt_roadmap_api_v1_roadmaps__roadmap_id__adapt_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roadmaps/{roadmap_id}/steps/{step_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Move a step to in progress or complete
         * @description Completing a step writes a progress event and marks dependents available; repeating it is a no-op. Completing a step whose prerequisite is not yet mastered is a typed 409.
         */
        patch: operations["update_step_api_v1_roadmaps__roadmap_id__steps__step_id__patch"];
        trace?: never;
    };
    "/api/v1/progress": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * The caller's progress
         * @description Mastery projected from the append-only evidence, weak and stale concepts, learning velocity, the current roadmap position and the next recommended action. Velocity is null when there is too little evidence for a rate.
         */
        get: operations["get_progress_api_v1_progress_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/quizzes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Generate a grounded quiz
         * @description Retrieves the course's own passages, reranks them and asks the model for items in one structured call. Every item carries the citation ids that support it; an item whose evidence does not resolve is dropped rather than padded, and the shortfall is reported.
         */
        post: operations["create_quiz_api_v1_quizzes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/quizzes/{quiz_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * A previously generated quiz
         * @description A quiz belonging to another tenant or another user is reported as not found. Drafts are persisted, so this serves the exact items that were generated.
         */
        get: operations["get_quiz_api_v1_quizzes__quiz_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/quizzes/{quiz_id}/items/{item_id}/answer": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Submit and score an answer
         * @description Scores the answer against the item's rubric, writes exactly one progress event and records a quiz attempt with the rubric breakdown and misconceptions. A score the model computes is ignored: the total is computed from the criterion weights.
         */
        post: operations["submit_answer_api_v1_quizzes__quiz_id__items__item_id__answer_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/progress/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * The caller's progress summary
         * @description Mastery projected from the append-only evidence, weak and stale concepts, learning velocity, recent attempts, the current roadmap position and the next recommended action.
         */
        get: operations["get_progress_summary_api_v1_progress_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/progress/attempts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Paginated attempt history
         * @description The caller's scored attempts, newest first.
         */
        get: operations["list_attempts_api_v1_progress_attempts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Recommend catalogue resources for the caller
         * @description Resolves the caller's knowledge gaps from an explicit roadmap or from measured mastery, ranks catalogue resources by mastery-weighted coverage, difficulty fit, trust and recency, and explains each pick from its score decomposition. A gap with no catalogue coverage is reported in `degraded`, never filled with an unrelated resource.
         */
        get: operations["list_recommendations_api_v1_recommendations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendations/resources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Search the curated catalogue
         * @description Filter the global, curated resource catalogue by type, trust level and a free-text query over title, description and provider. Paginated, ordered by title.
         */
        get: operations["search_resources_api_v1_recommendations_resources_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendations/{resource_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * One catalogue resource and its concept coverage
         * @description Returns the resource's provenance, metadata and the concept slugs it covers.
         */
        get: operations["get_resource_api_v1_recommendations__resource_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendations/seed": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Seed the curated catalogue (admin only)
         * @description Inserts any curated resource that is not already present, keyed on URL, and never overwrites an existing row's metadata. Idempotent, so it is safe to re-run. Admin-only because it writes global data shared by every tenant.
         */
        post: operations["seed_resources_api_v1_recommendations_seed_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AnswerSubmitRequest
         * @description One submitted answer, as free text (a choice index, letter or text).
         */
        AnswerSubmitRequest: {
            /** Answer */
            answer: string;
        };
        /**
         * AssessmentResultResponse
         * @description A scored answer, its mastery delta and the event kind it wrote.
         */
        AssessmentResultResponse: {
            /**
             * Quiz Attempt Id
             * Format: uuid
             */
            quiz_attempt_id: string;
            /** Item Id */
            item_id: string;
            /** Quiz Id */
            quiz_id: string | null;
            /** Score */
            score: number | null;
            /** Rubric */
            rubric: components["schemas"]["RubricScoreResponse"][];
            /** Misconceptions */
            misconceptions: components["schemas"]["MisconceptionResponse"][];
            /** Mastery Delta */
            mastery_delta: number;
            /** Progress Event Kind */
            progress_event_kind: string | null;
            /** Degraded */
            degraded: string[];
        };
        /**
         * AttemptPageResponse
         * @description One page of the caller's attempt history.
         */
        AttemptPageResponse: {
            /** Items */
            items: components["schemas"]["AttemptResponse"][];
            /** Total */
            total: number;
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
        };
        /**
         * AttemptResponse
         * @description One stored attempt, as read back from the append-only log.
         */
        AttemptResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Quiz Id */
            quiz_id: string | null;
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Item Id */
            item_id: string;
            /** Concept Ids */
            concept_ids: string[];
            /** Answer */
            answer: string | null;
            /** Score */
            score: number;
            /** Rubric */
            rubric: {
                [key: string]: unknown;
            }[];
            /** Misconceptions */
            misconceptions: string[];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /** Body_upload_document_api_v1_documents_post */
        Body_upload_document_api_v1_documents_post: {
            /**
             * File
             * @description The document to ingest.
             */
            file: string;
            /**
             * Course Id
             * @description An existing course owned by the caller.
             */
            course_id?: string | null;
            /**
             * Course Name
             * @description Course name; created for the caller if absent.
             */
            course_name?: string | null;
            /** @description Provenance label; inferred from the filename if omitted. */
            source_type?: components["schemas"]["SourceType"] | null;
            /**
             * Reingest
             * @description Replace the chunks of an existing identical document.
             * @default false
             */
            reingest: boolean;
        };
        /**
         * CataloguePageResponse
         * @description A page of catalogue search results.
         */
        CataloguePageResponse: {
            /** Items */
            items: components["schemas"]["ResourceResponse"][];
            /** Total */
            total: number;
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
        };
        /** ChatRequest */
        ChatRequest: {
            /** Question */
            question: string;
            /**
             * Course Id
             * @description Restrict retrieval to one of the caller's courses.
             */
            course_id?: string | null;
            /**
             * Conversation Id
             * @description Continue an existing conversation owned by the caller.
             */
            conversation_id?: string | null;
            /**
             * Engine
             * @description Which engine answers the turn. 'agent' (default) runs the bounded LangGraph tutor and records the routed intent; 'rag' runs the direct retrieval-augmented path. Both produce equivalent grounded answers.
             * @default agent
             * @enum {string}
             */
            engine: "agent" | "rag";
        };
        /** ChatResponse */
        ChatResponse: {
            /** Answer */
            answer: string;
            /** Citations */
            citations: components["schemas"]["CitationResponse"][];
            /** Grounded */
            grounded: boolean;
            /** Degraded */
            degraded: string[];
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            usage?: components["schemas"]["UsageSummary"] | null;
            /**
             * Intent
             * @default tutor
             */
            intent: string;
            /** Trace Id */
            trace_id?: string | null;
            /** Proposed Actions */
            proposed_actions?: components["schemas"]["ProposedAction"][];
        };
        /** CheckResult */
        CheckResult: {
            /** Name */
            name: string;
            /** Ok */
            ok: boolean;
            /** Critical */
            critical: boolean;
            /** Latency Ms */
            latency_ms: number;
            /** Detail */
            detail?: string | null;
        };
        /**
         * CitationResponse
         * @description A resolvable citation. The quoted span is intentionally not exposed.
         */
        CitationResponse: {
            /** Citation Id */
            citation_id: string;
            /**
             * Chunk Id
             * Format: uuid
             */
            chunk_id: string;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Filename */
            filename: string;
            /** Page */
            page: number | null;
            /** Source Type */
            source_type: string;
        };
        /**
         * ConfirmProposalRequest
         * @description The client confirms a withheld write by returning its signed token.
         */
        ConfirmProposalRequest: {
            /** Token */
            token: string;
        };
        /**
         * ConfirmProposalResponse
         * @description The outcome of an executed proposal, or the reason it was refused.
         */
        ConfirmProposalResponse: {
            /** Status */
            status: string;
            /** Tool */
            tool: string;
            /** Result */
            result?: {
                [key: string]: unknown;
            } | null;
            /** Detail */
            detail?: string | null;
        };
        /** ConversationDetailResponse */
        ConversationDetailResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Title */
            title: string;
            /** Course Id */
            course_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Messages */
            messages: components["schemas"]["MessageResponse"][];
        };
        /** ConversationSummary */
        ConversationSummary: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Title */
            title: string;
            /** Course Id */
            course_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** CourseCreateRequest */
        CourseCreateRequest: {
            /** Name */
            name: string;
            /** Code */
            code?: string | null;
            /** Description */
            description?: string | null;
        };
        /** CourseDetailResponse */
        CourseDetailResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Code */
            code: string | null;
            /** Description */
            description: string | null;
            /** Created At */
            created_at: unknown;
            /** Documents */
            documents: components["schemas"]["DocumentSummary"][];
        };
        /** CourseResponse */
        CourseResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Code */
            code: string | null;
            /** Description */
            description: string | null;
            /** Created At */
            created_at: unknown;
        };
        /**
         * DocumentDetailResponse
         * @description One document's full ingestion state.
         */
        DocumentDetailResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Filename */
            filename: string;
            /** Content Type */
            content_type: string | null;
            /** Size Bytes */
            size_bytes: number;
            /** Sha256 */
            sha256: string;
            /** Status */
            status: string;
            /** Source Type */
            source_type: string;
            /** Page Count */
            page_count: number | null;
            /** Quarantine State */
            quarantine_state: string;
            /** Injection Score */
            injection_score: number;
            /** Error Message */
            error_message: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Chunk Count */
            chunk_count: number;
            /** Injection Classes */
            injection_classes?: string[];
        };
        /**
         * DocumentIngestionResponse
         * @description The result of an upload, including what ingestion actually did.
         *
         *     ``reused`` distinguishes "these exact bytes were already in this course" from
         *     a fresh ingest, so a client can tell a duplicate upload apart from a new
         *     document without a second request. ``degraded`` carries parser warnings
         *     (unreadable pages, no extractable text) so a partially useful upload is
         *     visible rather than silently incomplete.
         */
        DocumentIngestionResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Filename */
            filename: string;
            /** Content Type */
            content_type: string | null;
            /** Size Bytes */
            size_bytes: number;
            /** Sha256 */
            sha256: string;
            /** Status */
            status: string;
            /** Source Type */
            source_type: string;
            /** Page Count */
            page_count: number | null;
            /** Quarantine State */
            quarantine_state: string;
            /** Injection Score */
            injection_score: number;
            /** Error Message */
            error_message: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Chunks Processed
             * @description Chunks written, or already present on a reused document; 0 when quarantined.
             */
            chunks_processed: number;
            /** Reused */
            reused: boolean;
            /** Degraded */
            degraded?: string[];
        };
        /**
         * DocumentResponse
         * @description One document as returned by the list endpoint.
         */
        DocumentResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Filename */
            filename: string;
            /** Content Type */
            content_type: string | null;
            /** Size Bytes */
            size_bytes: number;
            /** Sha256 */
            sha256: string;
            /** Status */
            status: string;
            /** Source Type */
            source_type: string;
            /** Page Count */
            page_count: number | null;
            /** Quarantine State */
            quarantine_state: string;
            /** Injection Score */
            injection_score: number;
            /** Error Message */
            error_message: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * DocumentStatus
         * @description Lifecycle of a document through the ingestion pipeline.
         * @enum {string}
         */
        DocumentStatus: "pending" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";
        /** DocumentSummary */
        DocumentSummary: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Filename */
            filename: string;
            /** Content Type */
            content_type: string | null;
            /** Size Bytes */
            size_bytes: number;
            /** Page Count */
            page_count: number | null;
            /** Status */
            status: string;
            /** Source Type */
            source_type: string;
            /** Quarantine State */
            quarantine_state: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthResponse */
        HealthResponse: {
            /**
             * Status
             * @description Always 'ok' when the process is alive.
             */
            status: string;
            /** Service */
            service: string;
            /** Version */
            version: string;
            /** Environment */
            environment: string;
            /**
             * Retrieval Config Version
             * @description Hash of retrieval-affecting configuration; changes when retrieval behaviour changes.
             */
            retrieval_config_version: string;
        };
        /** LoginRequest */
        LoginRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Password */
            password: string;
        };
        /** LogoutRequest */
        LogoutRequest: {
            /** Refresh Token */
            refresh_token: string;
        };
        /** MessageResponse */
        MessageResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            role: components["schemas"]["MessageRole"];
            /** Content */
            content: string;
            /** Citations */
            citations: components["schemas"]["CitationResponse"][];
            /** Degraded */
            degraded: string[];
            /** Grounded */
            grounded: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * MessageRole
         * @enum {string}
         */
        MessageRole: "system" | "user" | "assistant" | "tool";
        /**
         * MisconceptionResponse
         * @description A typed misconception with the passages that contradict it.
         */
        MisconceptionResponse: {
            /** Misconception Type */
            misconception_type: string;
            /** Description */
            description: string;
            /** Corrected Statement */
            corrected_statement: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high";
            /** Citation Ids */
            citation_ids: string[];
            /**
             * Confidence
             * @enum {string}
             */
            confidence: "low" | "medium" | "high";
        };
        /**
         * NextActionResponse
         * @description The single next thing the student should do.
         */
        NextActionResponse: {
            /** Kind */
            kind: string;
            /** Title */
            title: string;
            /** Rationale */
            rationale: string;
            /** Roadmap Id */
            roadmap_id: string | null;
            /** Step Id */
            step_id: string | null;
            /** Concept Id */
            concept_id: string | null;
        };
        /**
         * ProgressOverviewResponse
         * @description Mastery, weak/stale concepts, velocity and the next recommended action.
         */
        ProgressOverviewResponse: {
            /** Mastery */
            mastery: {
                [key: string]: number;
            };
            /** Weak Concepts */
            weak_concepts: string[];
            /** Stale Concepts */
            stale_concepts: string[];
            /** Velocity */
            velocity: number | null;
            /** Attempt Counts */
            attempt_counts: {
                [key: string]: number;
            };
            current_position: components["schemas"]["RoadmapPositionResponse"] | null;
            next_action: components["schemas"]["NextActionResponse"] | null;
            /** Degraded */
            degraded: string[];
        };
        /**
         * ProgressSummaryResponse
         * @description Mastery, weak/stale concepts, velocity, recent attempts and the next action.
         */
        ProgressSummaryResponse: {
            /** Mastery */
            mastery: {
                [key: string]: number;
            };
            /** Weak Concepts */
            weak_concepts: string[];
            /** Stale Concepts */
            stale_concepts: string[];
            /** Velocity */
            velocity: number | null;
            /** Attempt Counts */
            attempt_counts: {
                [key: string]: number;
            };
            /** Recent Attempts */
            recent_attempts: components["schemas"]["AttemptResponse"][];
            current_position: components["schemas"]["RoadmapPositionResponse"] | null;
            next_action: components["schemas"]["NextActionResponse"] | null;
            /** Degraded */
            degraded: string[];
        };
        /**
         * ProposedAction
         * @description A consequential write the model proposed but did not execute.
         *
         *     ``docs/architecture/security.md`` section 5: the platform separates proposal
         *     from execution. The model never holds an execution capability for a
         *     consequential action; it produces this object, the API returns it to the
         *     client, and a deterministic endpoint re-validates and executes it only after
         *     an explicit confirmation.
         *
         *     ``token`` is an HMAC-signed, expiring encoding of ``(agent, tool, arguments,
         *     tenant_id)``. The client cannot forge or tamper with a proposal: the
         *     signature is verified on confirmation, the tenant is re-checked against the
         *     authenticated request, and the arguments are re-validated against the tool's
         *     strict schema. ``arguments`` are deliberately *not* repeated in the public
         *     fields; they live inside the signed token.
         */
        ProposedAction: {
            /** Action */
            action: string;
            /** Target */
            target?: {
                [key: string]: unknown;
            };
            /** Rationale */
            rationale: string;
            /** Preview */
            preview: string;
            /** Token */
            token: string;
            /** Expires At */
            expires_at: number;
        };
        /**
         * QuizCitationResponse
         * @description A citation attached to a quiz, without the quoted passage text.
         */
        QuizCitationResponse: {
            /** Citation Id */
            citation_id: string;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Filename */
            filename: string;
            /** Page */
            page?: number | null;
            /** Source Type */
            source_type: string;
        };
        /**
         * QuizCreateRequest
         * @description Generate a grounded quiz for one of the caller's courses.
         */
        QuizCreateRequest: {
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /**
             * Concept Ids
             * @description Optional concepts to align the quiz to; ids that do not resolve are ignored.
             */
            concept_ids?: string[];
            /**
             * N Items
             * @default 5
             */
            n_items: number;
            /**
             * Difficulty
             * @default medium
             * @enum {string}
             */
            difficulty: "easy" | "medium" | "hard";
            /**
             * Item Types
             * @description Item kinds to generate. Unsupported kinds are dropped by the generator.
             */
            item_types?: ("multiple_choice" | "short_answer" | "concept_check")[];
        };
        /**
         * QuizItemResponse
         * @description One generated item, with its rubric and its supporting citation ids.
         */
        QuizItemResponse: {
            /** Item Id */
            item_id: string;
            /**
             * Item Type
             * @enum {string}
             */
            item_type: "multiple_choice" | "short_answer" | "concept_check";
            /** Prompt */
            prompt: string;
            /** Choices */
            choices: string[];
            /** Correct Choice Index */
            correct_choice_index: number | null;
            /** Model Answer */
            model_answer: string | null;
            /** Correct Boolean */
            correct_boolean: boolean | null;
            /** Rubric */
            rubric: components["schemas"]["RubricCriterionResponse"][];
            /** Citation Ids */
            citation_ids: string[];
            /** Concept Id */
            concept_id: string | null;
            /** Justification */
            justification: string | null;
        };
        /**
         * QuizResponse
         * @description A generated draft, with the shortfall it honestly recorded.
         */
        QuizResponse: {
            /**
             * Quiz Id
             * Format: uuid
             */
            quiz_id: string;
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Concept Ids */
            concept_ids: string[];
            /**
             * Difficulty
             * @enum {string}
             */
            difficulty: "easy" | "medium" | "hard";
            /** Item Types */
            item_types: ("multiple_choice" | "short_answer" | "concept_check")[];
            /** Items */
            items: components["schemas"]["QuizItemResponse"][];
            /** Citations */
            citations: components["schemas"]["QuizCitationResponse"][];
            /** Requested Items */
            requested_items: number;
            /** Shortfall */
            shortfall: number;
            /** Degraded */
            degraded: string[];
        };
        /** ReadinessResponse */
        ReadinessResponse: {
            /**
             * Status
             * @description 'ready' when every critical check passed, else 'degraded'.
             */
            status: string;
            /** Checks */
            checks: components["schemas"]["CheckResult"][];
            /** Duration Ms */
            duration_ms: number;
        };
        /**
         * RecommendationExplanation
         * @description The deterministic answer to "why this resource?".
         *
         *     Every field is derived from the typed score decomposition: ``covered_gap_*``
         *     can only name a gap the resource genuinely covers (it is the intersection of
         *     the resource's concept slugs with the gaps, not a paraphrase), and
         *     ``contributions`` is the exact vector the ranking summed. A model may rephrase
         *     ``summary`` elsewhere, but it may never add a claim that is not already in
         *     these fields.
         */
        RecommendationExplanation: {
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Summary */
            summary: string;
            /** Primary Gap Slug */
            primary_gap_slug: string | null;
            /** Primary Gap Name */
            primary_gap_name: string | null;
            /** Covered Gap Slugs */
            covered_gap_slugs: string[];
            /** Covered Gap Names */
            covered_gap_names: string[];
            /** Next Gap Slug */
            next_gap_slug: string | null;
            /** Next Gap Name */
            next_gap_name: string | null;
            /** Next Step */
            next_step: string;
            /** Coverage */
            coverage: number;
            /** Contributions */
            contributions: {
                [key: string]: number;
            };
            /** Personalised */
            personalised: boolean;
        };
        /**
         * RecommendationGapResponse
         * @description One gap the recommendation set was built to close.
         */
        RecommendationGapResponse: {
            /**
             * Concept Id
             * Format: uuid
             */
            concept_id: string;
            /** Slug */
            slug: string;
            /** Name */
            name: string;
            /** Difficulty */
            difficulty: number;
            /** Mastery */
            mastery: number;
            /** Never Assessed */
            never_assessed: boolean;
        };
        /**
         * RecommendationItemResponse
         * @description A recommended resource, its score decomposition and its explanation.
         */
        RecommendationItemResponse: {
            resource: components["schemas"]["ResourceResponse"];
            /** Score */
            score: number;
            /** Contributions */
            contributions: {
                [key: string]: number;
            };
            /** Covered Gap Slugs */
            covered_gap_slugs: string[];
            /** Personalised */
            personalised: boolean;
            explanation: components["schemas"]["RecommendationExplanation"];
        };
        /**
         * RecommendationListResponse
         * @description Recommendations for the caller, with the gaps and degradation markers.
         */
        RecommendationListResponse: {
            /** Recommendations */
            recommendations: components["schemas"]["RecommendationItemResponse"][];
            /** Gaps */
            gaps: components["schemas"]["RecommendationGapResponse"][];
            /** Degraded */
            degraded: string[];
            /** Personalised */
            personalised: boolean;
        };
        /** RefreshRequest */
        RefreshRequest: {
            /** Refresh Token */
            refresh_token: string;
        };
        /**
         * RegisterRequest
         * @description Self-service registration, which creates a tenant and its owner.
         *
         *     Registration creates a new tenant rather than joining one. Joining is an
         *     invitation flow, which is a different operation with different
         *     authorisation; conflating them is how "anyone can add themselves to any
         *     workspace" bugs happen.
         */
        RegisterRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Password */
            password: string;
            /** Full Name */
            full_name?: string | null;
            /**
             * Tenant Name
             * @description Organisation name. Defaults to a name derived from the email domain.
             */
            tenant_name?: string | null;
        };
        /** RegistrationResponse */
        RegistrationResponse: {
            user: components["schemas"]["UserResponse"];
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Tenant Slug */
            tenant_slug: string;
            tokens: components["schemas"]["TokenResponse"];
        };
        /**
         * ResourceResponse
         * @description One catalogue resource.
         */
        ResourceResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Title */
            title: string;
            /** Authors */
            authors: string[];
            /** Publisher */
            publisher: string | null;
            /** Year */
            year: number | null;
            /** Url */
            url: string;
            resource_type: components["schemas"]["ResourceType"];
            /** Provider */
            provider: string;
            trust: components["schemas"]["SourceTrust"];
            /** Difficulty */
            difficulty: number;
            /** Description */
            description: string;
            /** Duration Hours */
            duration_hours: number | null;
            /** Is Free */
            is_free: boolean;
            /** Rating */
            rating: number | null;
            /** Is Verified */
            is_verified: boolean;
            /** Verified At */
            verified_at: string | null;
            /** Notes */
            notes: string | null;
        };
        /**
         * ResourceType
         * @description What kind of thing a catalogue entry is.
         * @enum {string}
         */
        ResourceType: "course" | "mooc" | "book" | "documentation" | "paper" | "tutorial" | "video" | "standard";
        /**
         * ResourceWithConceptsResponse
         * @description A resource together with the concept slugs it covers.
         */
        ResourceWithConceptsResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Title */
            title: string;
            /** Authors */
            authors: string[];
            /** Publisher */
            publisher: string | null;
            /** Year */
            year: number | null;
            /** Url */
            url: string;
            resource_type: components["schemas"]["ResourceType"];
            /** Provider */
            provider: string;
            trust: components["schemas"]["SourceTrust"];
            /** Difficulty */
            difficulty: number;
            /** Description */
            description: string;
            /** Duration Hours */
            duration_hours: number | null;
            /** Is Free */
            is_free: boolean;
            /** Rating */
            rating: number | null;
            /** Is Verified */
            is_verified: boolean;
            /** Verified At */
            verified_at: string | null;
            /** Notes */
            notes: string | null;
            /** Concept Slugs */
            concept_slugs: string[];
        };
        /**
         * RoadmapAdaptRequest
         * @description Ask for a revision now, recording why it was requested.
         */
        RoadmapAdaptRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * RoadmapCreateRequest
         * @description Create (or reuse) a roadmap from a learning goal.
         */
        RoadmapCreateRequest: {
            /**
             * Course Id
             * @description Restrict the plan to one of the caller's courses.
             */
            course_id?: string | null;
            /**
             * Goal Text
             * @description What the student wants to be able to do, in their own words.
             */
            goal_text: string;
            /**
             * Goal Concept Id
             * @description An explicit concept to plan towards. When supplied it must resolve, or the request fails rather than planning something else.
             */
            goal_concept_id?: string | null;
            /**
             * Available Hours Per Week
             * @description Optional weekly budget, used only to estimate how many weeks the plan spans.
             */
            available_hours_per_week?: number | null;
        };
        /**
         * RoadmapDetailResponse
         * @description A roadmap revision and its steps, in order.
         */
        RoadmapDetailResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Course Id */
            course_id: string | null;
            /** Goal Concept Id */
            goal_concept_id: string | null;
            /** Goal Text */
            goal_text: string;
            /** Revision */
            revision: number;
            status: components["schemas"]["RoadmapStatus"];
            /** Estimated Hours */
            estimated_hours: number;
            /** Reason */
            reason: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Steps */
            steps: components["schemas"]["RoadmapStepResponse"][];
        };
        /**
         * RoadmapPositionResponse
         * @description Where the student is in their active plan.
         */
        RoadmapPositionResponse: {
            /**
             * Roadmap Id
             * Format: uuid
             */
            roadmap_id: string;
            /** Revision */
            revision: number;
            /**
             * Step Id
             * Format: uuid
             */
            step_id: string;
            /** Concept Id */
            concept_id: string | null;
            /** Order Index */
            order_index: number;
            /** Title */
            title: string;
            status: components["schemas"]["RoadmapStepStatus"];
        };
        /**
         * RoadmapResponse
         * @description One roadmap revision, without its steps.
         */
        RoadmapResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Course Id */
            course_id: string | null;
            /** Goal Concept Id */
            goal_concept_id: string | null;
            /** Goal Text */
            goal_text: string;
            /** Revision */
            revision: number;
            status: components["schemas"]["RoadmapStatus"];
            /** Estimated Hours */
            estimated_hours: number;
            /** Reason */
            reason: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * RoadmapStatus
         * @description Lifecycle of a roadmap revision.
         *
         *     ``superseded`` is the audit-preserving alternative to deletion: a student's
         *     history of plans stays readable when the plan changes.
         * @enum {string}
         */
        RoadmapStatus: "active" | "superseded" | "completed";
        /**
         * RoadmapStepResponse
         * @description One ordered step, with its blockers and deterministic estimate.
         */
        RoadmapStepResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Concept Id */
            concept_id: string | null;
            /** Order Index */
            order_index: number;
            /** Title */
            title: string;
            /** Description */
            description: string;
            status: components["schemas"]["RoadmapStepStatus"];
            /** Blocked By */
            blocked_by: string[];
            /** Estimated Hours */
            estimated_hours: number;
            /** Completed At */
            completed_at: string | null;
        };
        /**
         * RoadmapStepStatus
         * @description Where one ordered step is in the student's progress.
         * @enum {string}
         */
        RoadmapStepStatus: "pending" | "available" | "in_progress" | "completed" | "blocked";
        /**
         * RoadmapStepUpdateRequest
         * @description Move one step to in progress or completed.
         */
        RoadmapStepUpdateRequest: {
            /**
             * Status
             * @enum {string}
             */
            status: "in_progress" | "completed";
        };
        /**
         * RubricCriterionResponse
         * @description One named, weighted criterion of an item's rubric.
         */
        RubricCriterionResponse: {
            /** Criterion */
            criterion: string;
            /** Weight */
            weight: number;
            /**
             * Description
             * @default
             */
            description: string;
        };
        /**
         * RubricScoreResponse
         * @description The model's score and justification for one criterion.
         */
        RubricScoreResponse: {
            /** Criterion */
            criterion: string;
            /** Weight */
            weight: number;
            /** Score */
            score: number;
            /**
             * Justification
             * @default
             */
            justification: string;
        };
        /**
         * SeedReportResponse
         * @description What one catalogue seeding run did; ``skipped`` proves idempotency.
         */
        SeedReportResponse: {
            /** Total */
            total: number;
            /** Inserted */
            inserted: number;
            /** Skipped */
            skipped: number;
            /** Concept Links Inserted */
            concept_links_inserted: number;
        };
        /**
         * SourceTrust
         * @description How much authority a source carries (``security.md`` section 8.2).
         *
         *     The ordering is deliberate and monotone: a ranking term reads these levels,
         *     and the seed-integrity test asserts that a level is only used for a host that
         *     can legitimately carry it.
         * @enum {string}
         */
        SourceTrust: "official" | "academic" | "community" | "secondary";
        /**
         * SourceType
         * @description Provenance class, used for trust labelling in recommendations.
         *
         *     Distinct from the file extension: a PDF can be a lecture, a paper or a book
         *     chapter, and the distinction changes how the source should be presented and
         *     weighted.
         * @enum {string}
         */
        SourceType: "lecture" | "slide" | "paper" | "book" | "syllabus" | "notes" | "documentation" | "other";
        /**
         * TokenResponse
         * @description Issued credentials.
         *
         *     ``refresh_token`` is returned in the body rather than set as a cookie because
         *     the client is a separate origin in the deployed topology and the API does not
         *     rely on cookies for authentication. The trade-off is that the client must
         *     store it deliberately; the frontend keeps it out of the render path and
         *     clears it on logout.
         */
        TokenResponse: {
            /** Access Token */
            access_token: string;
            /** Refresh Token */
            refresh_token: string;
            /**
             * Token Type
             * @default bearer
             */
            token_type: string;
            /**
             * Expires In
             * @description Access-token lifetime in seconds.
             */
            expires_in: number;
        };
        /**
         * UsageSummary
         * @description Token accounting for one model call, without the provider model name.
         */
        UsageSummary: {
            /** Prompt Tokens */
            prompt_tokens: number;
            /** Completion Tokens */
            completion_tokens: number;
            /** Total Tokens */
            total_tokens: number;
            /** Cost Usd */
            cost_usd: number;
        };
        /**
         * UserResponse
         * @description A user as returned by the API.
         *
         *     Note the absence of ``hashed_password``. The previous implementation's user
         *     schema inherited from its database schema, so the bcrypt hash was serialised
         *     into ``/auth/me`` and the registration response. Response models here are
         *     written independently of the ORM models so that adding a sensitive column to
         *     a table cannot silently publish it.
         */
        UserResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Full Name */
            full_name: string | null;
            role: components["schemas"]["UserRole"];
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Is Active */
            is_active: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * UserRole
         * @description Coarse authorisation role within a tenant.
         *
         *     Deliberately small. Roles gate *administrative* actions (inviting members,
         *     managing the catalogue), not access to learning content, which is scoped by
         *     ownership rather than by role.
         * @enum {string}
         */
        UserRole: "owner" | "admin" | "member";
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    readyz_readyz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
            /** @description At least one critical dependency is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    metrics_metrics_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain; version=0.0.4; charset=utf-8": unknown;
                };
            };
            /** @description Metrics collection is disabled for this deployment. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    register_api_v1_auth_register_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegisterRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RegistrationResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    login_api_v1_auth_token_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TokenResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    refresh_api_v1_auth_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RefreshRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TokenResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    logout_api_v1_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LogoutRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_api_v1_auth_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserResponse"];
                };
            };
        };
    };
    list_courses_api_v1_courses_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseResponse"][];
                };
            };
        };
    };
    create_course_api_v1_courses_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CourseCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_course_api_v1_courses__course_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_course_api_v1_courses__course_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_documents_route_api_v1_documents_get: {
        parameters: {
            query?: {
                /** @description Only this course. */
                course_id?: string | null;
                /** @description Only this ingestion status. */
                status?: components["schemas"]["DocumentStatus"] | null;
                /** @description Page size. */
                limit?: number;
                /** @description Rows to skip. */
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentResponse"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload_document_api_v1_documents_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_upload_document_api_v1_documents_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentIngestionResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_document_route_api_v1_documents__document_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_document_route_api_v1_documents__document_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    chat_api_v1_chat_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChatResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_proposal_api_v1_chat_confirm_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfirmProposalRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConfirmProposalResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_chat_api_v1_chat_stream_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_conversations_api_v1_chat_conversations_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_conversation_api_v1_chat_conversations__conversation_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_roadmaps_api_v1_roadmaps_get: {
        parameters: {
            query?: {
                course_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoadmapResponse"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_roadmap_api_v1_roadmaps_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoadmapCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoadmapDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_roadmap_api_v1_roadmaps__roadmap_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roadmap_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoadmapDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    adapt_roadmap_api_v1_roadmaps__roadmap_id__adapt_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roadmap_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoadmapAdaptRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoadmapDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_step_api_v1_roadmaps__roadmap_id__steps__step_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roadmap_id: string;
                step_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoadmapStepUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoadmapDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_progress_api_v1_progress_get: {
        parameters: {
            query?: {
                course_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProgressOverviewResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_quiz_api_v1_quizzes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuizCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuizResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_quiz_api_v1_quizzes__quiz_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                quiz_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuizResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_answer_api_v1_quizzes__quiz_id__items__item_id__answer_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                quiz_id: string;
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnswerSubmitRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssessmentResultResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_progress_summary_api_v1_progress_summary_get: {
        parameters: {
            query?: {
                course_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProgressSummaryResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_attempts_api_v1_progress_attempts_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AttemptPageResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_recommendations_api_v1_recommendations_get: {
        parameters: {
            query?: {
                course_id?: string | null;
                limit?: number;
                difficulty_max?: number | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationListResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_resources_api_v1_recommendations_resources_get: {
        parameters: {
            query?: {
                resource_type?: components["schemas"]["ResourceType"] | null;
                trust?: components["schemas"]["SourceTrust"] | null;
                q?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CataloguePageResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_resource_api_v1_recommendations__resource_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                resource_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceWithConceptsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    seed_resources_api_v1_recommendations_seed_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeedReportResponse"];
                };
            };
        };
    };
}
