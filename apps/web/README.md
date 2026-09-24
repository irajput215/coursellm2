# CourseLLM web client

React + TypeScript client for the CourseLLM API. It replaced a prototype client
that failed its build, redeclared a different `Course` type on every page,
discarded the citations the API returned, and rendered hardcoded metrics as if
they were real. This client is the opposite of that by construction: one typed
API layer, all server state in TanStack Query, and no invented data anywhere.

## Commands

```bash
npm install
npm run dev            # Vite dev server on :5173, proxies /api to :8000
npm run typecheck      # tsc --noEmit against tsconfig.json and tsconfig.node.json
npm run lint           # eslint (flat config)
npm run test -- --run  # vitest + @testing-library/react + msw
npm run build          # typecheck, then Vite build into dist/
npm run schema:generate # regenerate src/api/schema.d.ts from the live OpenAPI doc
```

`npm run typecheck` and `npm run build` enforce `strict`, `noUncheckedIndexedAccess`
and `exactOptionalPropertyTypes`. `npm run build` runs the typecheck first, so a
build cannot pass with a type error in it.

## Architecture

- **One typed API layer.** `src/api/schema.d.ts` is generated from the live
  FastAPI OpenAPI document (`npm run schema:generate`, using `openapi-typescript`
  as a dev dependency). `src/api/types.ts` is the only place response shapes are
  named; every page imports those aliases. There are no per-page `interface
  Course` declarations and no `response.data` trust anywhere.
- **`ApiError`.** `src/api/client.ts` maps the backend's error envelope
  (`error`, `detail`, `request_id`) onto a single error type. Error states render
  the safe `detail` and, when present, the `request_id`, so a support ticket can
  quote it.
- **TanStack Query owns server state.** Query keys are typed and centralised in
  `src/api/keys.ts`; hooks live in `src/api/hooks.ts`. There is no
  `useEffect`-plus-`useState` fetching and no `console.error`-and-pretend.
- **Auth.** The access token is held in memory only — never `localStorage`. The
  refresh token is persisted in `sessionStorage` (tab-scoped) deliberately, so a
  reload does not sign the student out; signing out revokes it server-side. A
  401 triggers exactly one refresh attempt and one retry; a second 401 fails the
  call instead of looping. `isAuthenticated` distinguishes *unauthenticated*
  (the server rejected the session) from *unreachable* (the server could not be
  reached), so one network blip does not force a login.
- **Streaming chat.** `POST /chat/stream` is consumed with `fetch` + a stream
  reader (`EventSource` cannot POST). Tokens render as they arrive, with a Stop
  control; citations arrive as a `citations` event and render as expandable
  chips. Markdown is rendered with `remark-gfm` and code fences are
  syntax-highlighted with a language label and a copy button.
- **Written-down degradation.** The SSE contract does not carry `grounded` or
  `degraded`, so after a turn completes the client reconciles the persisted
  assistant message from `GET /chat/conversations/{id}` and only then shows the
  "answer quality is reduced" banner. It never invents those flags.
- **Citations.** The API deliberately exposes citation metadata (filename, page,
  source type, document id) and *not* the retrieved passage text. A citation
  chip therefore expands to that metadata plus a link to the source document,
  and says plainly that the passage text is not returned. If a deployment ever
  adds a `quote`/`excerpt`/`passage` field it is rendered automatically
  (`src/lib/citations.ts`).
- **Proposed actions.** A consequential write comes back as a `proposed_actions`
  entry with a signed, expiring token. The client renders it with a Confirm
  button that posts the token to `/chat/confirm`; the server re-validates
  signature, expiry, tenant and arguments.
- **Dark mode** is a class strategy on `<html>`, defaulting to
  `prefers-color-scheme` and overridable to light or dark.
- **Responsive.** Below `lg` (1024px) the sidebar is an off-canvas drawer that
  closes on navigation, on Escape and on the backdrop; at `lg` and up it is a
  pinned rail. There is a skip link, labelled form controls, `aria-label` on
  icon-only buttons, keyboard-operable disclosures and a per-route
  `document.title`.
- **Route-level error boundary** (`src/components/ErrorBoundary.tsx`) with a
  reset action, and a real 404 page instead of a blank render.

## Deployment: no Vercel

The build outputs static assets to `dist/`. Nothing in this project targets
Vercel, and there is no `vercel.json` or any Vercel configuration. In
production:

- `docker/web.Dockerfile` builds `dist/` and serves it with nginx (SPA
  fallback, hashed-asset caching, `/healthz` for the orchestrator); or
- the same `dist/` directory is synced to S3 and served through CloudFront,
  with `/api/*` routed to the API origin.

`VITE_API_BASE_URL` selects the API origin at build time; leave it empty for a
same-origin deployment. The dev server proxies `/api`, `/healthz` and `/readyz`
to `http://localhost:8000` (`VITE_DEV_API_TARGET` overrides that).

## Tests

`vitest` + `@testing-library/react` + `msw`. Every request goes through the MSW
server in `src/test/server.ts`, and `onUnhandledRequest: 'error'` means a test
that forgets a handler fails instead of reaching a real API. Coverage includes:

- `src/api/client.test.ts` — envelope → `ApiError` with the request id; one
  refresh on 401 then a retry; no refresh loop on a second 401; the access token
  never reaches `localStorage`.
- `src/features/chat/chat.test.tsx` — tokens render incrementally while the
  stream is still open; citations render as chips that expand; a failed stream
  renders an error state rather than an answer; the degraded banner appears; the
  Stop control cancels.
- `src/features/roadmap/roadmap.test.tsx` — each API status maps to a visual
  state; a blocked step cannot be completed or started from the UI.
- `src/features/auth/auth.test.tsx` — redirect-back preserves the intended
  route; an unreachable server shows a retry instead of the login form; a login
  failure surfaces the envelope and its request id.
- `src/components/AppShell.test.tsx` and `src/styles/responsive.test.ts` — the
  375px drawer behaviour and the stylesheet contract behind it.
- `src/pages/pages.test.tsx` — loading, empty and error states for every data
  page, plus real dashboard values.
- `src/pages/no-mock-data.test.ts` — the prototype's mock strings never appear
  in the source tree, and once `dist/` exists, never in the built bundle either.

## Environment

Copy `.env.example` to `.env.local` for local overrides. `VITE_*` values are
inlined into the bundle, so nothing secret belongs in them — the API is
authenticated with short-lived bearer tokens issued at runtime.
