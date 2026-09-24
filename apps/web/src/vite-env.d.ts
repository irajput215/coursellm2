/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API; empty means same-origin. */
  readonly VITE_API_BASE_URL?: string
  /** Dev-server proxy target; never used in a production build. */
  readonly VITE_DEV_API_TARGET?: string
  readonly MODE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
