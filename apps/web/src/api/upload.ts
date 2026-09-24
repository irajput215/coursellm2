/**
 * Document upload.
 *
 * Uploads use `XMLHttpRequest` rather than `fetch` because only XHR exposes
 * upload progress events, and "uploading a 40 MB lecture recording with no
 * feedback" was one of the usability problems this client replaces. Everything
 * else about the request (bearer token, one refresh on 401, the error envelope)
 * matches `apiRequest`.
 */
import {
  ApiError,
  apiErrorFromResponse,
  getAccessToken,
  makeNetworkError,
  refreshSession,
  resolveUrl,
} from './client'
import type { DocumentIngestion, SourceType } from './types'

export interface UploadDocumentInput {
  file: File
  courseId?: string | null
  courseName?: string | null
  sourceType?: SourceType | null
  reingest?: boolean
  signal?: AbortSignal
  onProgress?: (percent: number) => void
}

function buildFormData(input: UploadDocumentInput): FormData {
  const form = new FormData()
  form.append('file', input.file, input.file.name)
  if (input.courseId !== null && input.courseId !== undefined) {
    form.append('course_id', input.courseId)
  }
  if (input.courseName !== null && input.courseName !== undefined && input.courseName !== '') {
    form.append('course_name', input.courseName)
  }
  if (input.sourceType !== null && input.sourceType !== undefined) {
    form.append('source_type', input.sourceType)
  }
  if (input.reingest === true) form.append('reingest', 'true')
  return form
}

function xhrUpload(
  form: FormData,
  signal: AbortSignal | undefined,
  onProgress: ((percent: number) => void) | undefined,
): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    const request = new XMLHttpRequest()
    const abort = (): void => request.abort()
    const cleanup = (): void => {
      signal?.removeEventListener('abort', abort)
    }

    request.open('POST', resolveUrl('/documents'))
    request.responseType = 'text'
    request.setRequestHeader('Accept', 'application/json')
    const token = getAccessToken()
    if (token !== null) request.setRequestHeader('Authorization', `Bearer ${token}`)

    if (onProgress !== undefined) {
      request.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      })
    }

    request.addEventListener('load', () => {
      cleanup()
      resolve(
        new Response(request.responseText === '' ? null : request.responseText, {
          status: request.status,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    request.addEventListener('error', () => {
      cleanup()
      reject(makeNetworkError(new Error('The upload connection failed.')))
    })
    request.addEventListener('abort', () => {
      cleanup()
      reject(new DOMException('The upload was cancelled.', 'AbortError'))
    })

    if (signal !== undefined) {
      if (signal.aborted) {
        reject(new DOMException('The upload was cancelled.', 'AbortError'))
        return
      }
      signal.addEventListener('abort', abort)
    }

    request.send(form)
  })
}

/** Upload one document, with progress, and return the ingestion result. */
export async function uploadDocument(input: UploadDocumentInput): Promise<DocumentIngestion> {
  const form = buildFormData(input)
  let response = await xhrUpload(form, input.signal, input.onProgress)
  if (response.status === 401) {
    try {
      await refreshSession()
    } catch (error) {
      throw error instanceof ApiError
        ? error
        : makeNetworkError(new Error('The session could not be refreshed.'))
    }
    // The body has already been consumed for the failed attempt; rebuild it.
    response = await xhrUpload(buildFormData(input), input.signal, input.onProgress)
  }
  if (!response.ok) throw await apiErrorFromResponse(response)
  const text = await response.text()
  return JSON.parse(text) as DocumentIngestion
}
