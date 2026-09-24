import { useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'

import type { ChatEngine, Course } from '@/api/types'

/**
 * The composer.
 *
 * `htmlFor`/`id` pair every control with its label (the previous client relied
 * on placeholders), and the engine toggle exposes its state with
 * `aria-pressed` rather than colour alone.
 */
export function ChatComposer({
  onSend,
  onCancel,
  isStreaming,
  engine,
  onEngineChange,
  courses,
  courseId,
  onCourseChange,
}: {
  onSend: (question: string) => void
  onCancel: () => void
  isStreaming: boolean
  engine: ChatEngine
  onEngineChange: (engine: ChatEngine) => void
  courses: readonly Course[]
  courseId: string | null
  onCourseChange: (courseId: string | null) => void
}): ReactNode {
  const [value, setValue] = useState('')
  const questionId = useId()
  const courseSelectId = useId()
  const engineLabelId = useId()

  const submit = (event?: FormEvent): void => {
    event?.preventDefault()
    const trimmed = value.trim()
    if (trimmed === '' || isStreaming) return
    onSend(trimmed)
    setValue('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="composer" onSubmit={submit} aria-label="Ask a question">
      <div className="field">
        <label htmlFor={questionId}>Ask a question about your course material</label>
        <textarea
          id={questionId}
          value={value}
          rows={3}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Enter to send, Shift+Enter for a new line"
        />
      </div>

      <div className="composer-actions">
        <div className="row">
          <span className="stat-label" id={engineLabelId}>
            Engine
          </span>
          <div className="engine-toggle" role="group" aria-labelledby={engineLabelId}>
            <button
              type="button"
              aria-pressed={engine === 'agent'}
              onClick={() => onEngineChange('agent')}
            >
              Agent
            </button>
            <button
              type="button"
              aria-pressed={engine === 'rag'}
              onClick={() => onEngineChange('rag')}
            >
              Direct RAG
            </button>
          </div>

          <label htmlFor={courseSelectId} className="stat-label">
            Course
          </label>
          <select
            id={courseSelectId}
            value={courseId ?? ''}
            onChange={(event) => onCourseChange(event.target.value === '' ? null : event.target.value)}
            style={{ width: 'auto', maxWidth: '14rem' }}
          >
            <option value="">All my courses</option>
            {courses.map((course) => (
              <option key={course.id} value={course.id}>
                {course.code === null ? course.name : `${course.code} — ${course.name}`}
              </option>
            ))}
          </select>
        </div>

        <div className="row">
          {isStreaming ? (
            <button type="button" className="btn" onClick={onCancel}>
              Stop
            </button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={value.trim() === ''}>
              Send
            </button>
          )}
        </div>
      </div>
    </form>
  )
}
