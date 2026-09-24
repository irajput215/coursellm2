import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useCourses, useCreateQuiz, useQuiz, useSubmitAnswer } from '@/api/hooks'
import type { AssessmentResult, QuizItem, QuizItemType } from '@/api/types'
import { CitationChips } from '@/components/CitationChips'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, InlineError, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { formatDecimal, formatPercent } from '@/lib/format'
import { DegradedBanner } from '@/features/chat/MessageBubble'

const ITEM_TYPE_LABEL: Record<QuizItemType, string> = {
  multiple_choice: 'Multiple choice',
  short_answer: 'Short answer',
  concept_check: 'Concept check',
}

function ItemResult({ result, item }: { result: AssessmentResult; item: QuizItem }): ReactNode {
  const correctChoice =
    item.item_type === 'multiple_choice' && item.correct_choice_index !== null
      ? item.choices[item.correct_choice_index]
      : undefined
  return (
    <div className="card" style={{ marginTop: '0.6rem' }} aria-label="Result">
      <p className="row">
        <strong>Score: {result.score === null ? 'not scored' : formatPercent(result.score)}</strong>
        <Badge tone={result.mastery_delta >= 0 ? 'success' : 'warning'}>
          mastery {result.mastery_delta >= 0 ? '+' : ''}
          {formatDecimal(result.mastery_delta, 3)}
        </Badge>
      </p>
      <DegradedBanner degraded={result.degraded} />
      {correctChoice === undefined ? null : (
        <p className="muted" style={{ margin: '0 0 0.5rem' }}>
          Correct choice: <strong>{correctChoice}</strong>
        </p>
      )}
      <h3 style={{ fontSize: '0.95rem' }}>Rubric breakdown</h3>
      {result.rubric.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          The API returned no rubric for this item.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <caption className="visually-hidden">Rubric criteria and awarded scores</caption>
            <thead>
              <tr>
                <th scope="col">Criterion</th>
                <th scope="col">Weight</th>
                <th scope="col">Score</th>
                <th scope="col">Justification</th>
              </tr>
            </thead>
            <tbody>
              {result.rubric.map((criterion) => (
                <tr key={criterion.criterion}>
                  <td>{criterion.criterion}</td>
                  <td>{formatDecimal(criterion.weight, 2)}</td>
                  <td>{formatDecimal(criterion.score, 2)}</td>
                  <td>{criterion.justification === '' ? '—' : criterion.justification}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result.misconceptions.length === 0 ? null : (
        <>
          <h3 style={{ fontSize: '0.95rem', marginTop: '0.75rem' }}>Misconceptions</h3>
          <ul className="stack" style={{ listStyle: 'none', padding: 0 }}>
            {result.misconceptions.map((misconception) => (
              <li key={misconception.misconception_type}>
                <p className="row" style={{ margin: 0 }}>
                  <Badge
                    tone={
                      misconception.severity === 'high'
                        ? 'danger'
                        : misconception.severity === 'medium'
                          ? 'warning'
                          : 'neutral'
                    }
                  >
                    {misconception.severity} severity
                  </Badge>
                  <Badge tone="neutral">{misconception.confidence} confidence</Badge>
                </p>
                <p style={{ margin: '0.25rem 0' }}>{misconception.description}</p>
                <p className="muted" style={{ margin: 0 }}>
                  Corrected: {misconception.corrected_statement}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

export function QuizPage(): ReactNode {
  useDocumentTitle('Quiz')
  const [searchParams] = useSearchParams()
  const [activeQuizId, setActiveQuizId] = useState<string | null>(searchParams.get('quiz'))
  const quiz = useQuiz(activeQuizId ?? undefined)
  const courses = useCourses()
  const createQuiz = useCreateQuiz()
  const submitAnswer = useSubmitAnswer()

  const [courseId, setCourseId] = useState('')
  const [nItems, setNItems] = useState('5')
  const [difficulty, setDifficulty] = useState<'easy' | 'medium' | 'hard'>('medium')
  const [itemTypes, setItemTypes] = useState<QuizItemType[]>(['multiple_choice'])
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [results, setResults] = useState<Record<string, AssessmentResult>>({})
  const [activeItem, setActiveItem] = useState<string | null>(null)

  const courseFieldId = useId()
  const countFieldId = useId()
  const difficultyFieldId = useId()

  const submitCreate = (event: FormEvent): void => {
    event.preventDefault()
    if (courseId === '') return
    createQuiz.mutate(
      {
        course_id: courseId,
        n_items: Number(nItems),
        difficulty,
        item_types: itemTypes,
      },
      {
        onSuccess: (created) => {
          setActiveQuizId(created.quiz_id)
          setAnswers({})
          setResults({})
        },
      },
    )
  }

  const submitItem = (item: QuizItem): void => {
    const answer = answers[item.item_id] ?? ''
    if (answer.trim() === '' || activeQuizId === null) return
    setActiveItem(item.item_id)
    submitAnswer.mutate(
      { quizId: activeQuizId, itemId: item.item_id, answer: { answer } },
      {
        onSuccess: (result) => {
          setResults((current) => ({ ...current, [result.item_id]: result }))
        },
        onSettled: () => setActiveItem(null),
      },
    )
  }

  return (
    <>
      <PageHeader
        title="Quiz"
        description="Quizzes are generated from your own material, scored against a rubric, and every score is recorded as evidence."
      />

      <div className="card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem' }}>Generate a quiz</h2>
        <form onSubmit={submitCreate}>
          <div className="field">
            <label htmlFor={courseFieldId}>Course</label>
            <select
              id={courseFieldId}
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
              required
            >
              <option value="">Choose a course…</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code === null ? course.name : `${course.code} — ${course.name}`}
                </option>
              ))}
            </select>
            {courses.data !== undefined && courses.data.length === 0 ? (
              <span className="hint">You need a course with material first.</span>
            ) : null}
          </div>
          <div className="field">
            <label htmlFor={countFieldId}>Number of items</label>
            <input
              id={countFieldId}
              type="number"
              min={1}
              max={20}
              value={nItems}
              onChange={(event) => setNItems(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor={difficultyFieldId}>Difficulty</label>
            <select
              id={difficultyFieldId}
              value={difficulty}
              onChange={(event) =>
                setDifficulty(event.target.value as 'easy' | 'medium' | 'hard')
              }
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </div>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="hint">Item types</legend>
            <div className="row">
              {(Object.keys(ITEM_TYPE_LABEL) as QuizItemType[]).map((type) => (
                <span className="row" key={type}>
                  <input
                    id={`item-type-${type}`}
                    type="checkbox"
                    checked={itemTypes.includes(type)}
                    onChange={(event) => {
                      setItemTypes((current) =>
                        event.target.checked
                          ? [...current, type]
                          : current.filter((value) => value !== type),
                      )
                    }}
                    style={{ width: 'auto' }}
                  />
                  <label htmlFor={`item-type-${type}`}>{ITEM_TYPE_LABEL[type]}</label>
                </span>
              ))}
            </div>
          </fieldset>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={createQuiz.isPending || courseId === '' || itemTypes.length === 0}
          >
            {createQuiz.isPending ? 'Generating…' : 'Generate quiz'}
          </button>
        </form>
        <InlineError error={createQuiz.error} />
      </div>

      {activeQuizId === null ? (
        <EmptyState
          title="No quiz loaded"
          description="Generate one above, or open a quiz by its id with ?quiz=<id>."
        />
      ) : quiz.isPending ? (
        <SkeletonList rows={4} />
      ) : quiz.error !== null ? (
        <ErrorState
          error={quiz.error}
          title="Could not load that quiz"
          onRetry={() => {
            void quiz.refetch()
          }}
        />
      ) : quiz.data.items.length === 0 ? (
        <EmptyState
          title="The generator produced no items"
          description="That usually means the course material did not contain enough evidence for the requested item types. Try another course or fewer items."
        />
      ) : (
        <div className="stack">
          <div className="spread">
            <p className="row" style={{ margin: 0 }}>
              <Badge tone="accent">{quiz.data.difficulty}</Badge>
              <span className="muted">
                {quiz.data.items.length} of {quiz.data.requested_items} requested items
              </span>
            </p>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                setActiveQuizId(null)
                setResults({})
                setAnswers({})
              }}
            >
              Close quiz
            </button>
          </div>

          {quiz.data.shortfall > 0 ? (
            <p className="banner banner-warning" role="status">
              <span aria-hidden="true">⚠</span>
              <span>
                {quiz.data.shortfall} requested {quiz.data.shortfall === 1 ? 'item' : 'items'} could
                not be generated from the available evidence.
              </span>
            </p>
          ) : null}

          <DegradedBanner degraded={quiz.data.degraded} />
          <CitationChips citations={quiz.data.citations} heading="Evidence for this quiz" />

          <ol className="stack" style={{ listStyle: 'none', padding: 0 }}>
            {quiz.data.items.map((item) => {
              const result = results[item.item_id]
              const value = answers[item.item_id] ?? ''
              return (
                <li className="card" key={item.item_id}>
                  <p className="row" style={{ margin: '0 0 0.4rem' }}>
                    <Badge tone="neutral">{ITEM_TYPE_LABEL[item.item_type]}</Badge>
                    {item.concept_id === null ? null : (
                      <span className="mono muted">{item.concept_id.slice(0, 8)}</span>
                    )}
                  </p>
                  <p style={{ margin: '0 0 0.5rem', fontWeight: 600 }}>{item.prompt}</p>

                  {item.item_type === 'multiple_choice' ? (
                    <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                      <legend className="visually-hidden">Choose one answer</legend>
                      <div className="stack">
                        {item.choices.map((choice, index) => {
                          const choiceId = `${item.item_id}-choice-${String(index)}`
                          return (
                            <span className="row" key={choiceId}>
                              <input
                                id={choiceId}
                                type="radio"
                                name={item.item_id}
                                value={choice}
                                checked={value === choice}
                                onChange={() =>
                                  setAnswers((current) => ({ ...current, [item.item_id]: choice }))
                                }
                                style={{ width: 'auto' }}
                              />
                              <label htmlFor={choiceId}>{choice}</label>
                            </span>
                          )
                        })}
                      </div>
                    </fieldset>
                  ) : (
                    <div className="field">
                      <label htmlFor={`${item.item_id}-answer`}>Your answer</label>
                      <textarea
                        id={`${item.item_id}-answer`}
                        value={value}
                        onChange={(event) =>
                          setAnswers((current) => ({
                            ...current,
                            [item.item_id]: event.target.value,
                          }))
                        }
                      />
                    </div>
                  )}

                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={value.trim() === '' || activeItem === item.item_id}
                    onClick={() => submitItem(item)}
                  >
                    {activeItem === item.item_id ? 'Scoring…' : 'Submit answer'}
                  </button>

                  {result === undefined ? null : <ItemResult result={result} item={item} />}
                </li>
              )
            })}
          </ol>
          <InlineError error={submitAnswer.error} />
        </div>
      )}
    </>
  )
}
