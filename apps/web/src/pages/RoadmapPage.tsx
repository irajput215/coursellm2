import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  useAdaptRoadmap,
  useCourses,
  useCreateRoadmap,
  useProgressSummary,
  useRoadmap,
  useRoadmaps,
  useUpdateRoadmapStep,
} from '@/api/hooks'
import type { RoadmapStepStatus } from '@/api/types'
import { EmptyState, ErrorState, SkeletonList } from '@/components/states'
import { Badge, InlineError, PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { RoadmapTree } from '@/features/roadmap/RoadmapTree'
import { formatDateTime, formatHours } from '@/lib/format'

export function RoadmapPage(): ReactNode {
  useDocumentTitle('Roadmap')
  const [searchParams] = useSearchParams()
  const roadmaps = useRoadmaps()
  const progress = useProgressSummary()
  const courses = useCourses()
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get('roadmap'))
  // Derived rather than synchronised in an effect: the first roadmap is the
  // fallback selection, and an explicit choice always wins.
  const effectiveId = selectedId ?? roadmaps.data?.[0]?.id ?? null
  const roadmap = useRoadmap(effectiveId ?? undefined)
  const updateStep = useUpdateRoadmapStep()
  const adapt = useAdaptRoadmap()
  const createRoadmap = useCreateRoadmap()

  const [goalText, setGoalText] = useState('')
  const [courseId, setCourseId] = useState('')
  const [hoursPerWeek, setHoursPerWeek] = useState('')
  const [busyStepId, setBusyStepId] = useState<string | null>(null)
  const [adaptOpen, setAdaptOpen] = useState(false)
  const [adaptReason, setAdaptReason] = useState('')

  const goalId = useId()
  const courseSelectId = useId()
  const hoursId = useId()
  const adaptReasonId = useId()

  const submitCreate = (event: FormEvent): void => {
    event.preventDefault()
    if (goalText.trim() === '') return
    createRoadmap.mutate(
      {
        goal_text: goalText.trim(),
        course_id: courseId === '' ? null : courseId,
        available_hours_per_week: hoursPerWeek === '' ? null : Number(hoursPerWeek),
      },
      {
        onSuccess: (created) => {
          setGoalText('')
          setHoursPerWeek('')
          setSelectedId(created.id)
        },
      },
    )
  }

  const recommendedStepId = progress.data?.next_action?.step_id ?? null

  const handleUpdate = (
    stepId: string,
    status: Extract<RoadmapStepStatus, 'in_progress' | 'completed'>,
  ): void => {
    if (effectiveId === null) return
    setBusyStepId(stepId)
    updateStep.mutate(
      { roadmapId: effectiveId, stepId, payload: { status } },
      { onSettled: () => setBusyStepId(null) },
    )
  }

  return (
    <>
      <PageHeader
        title="Roadmap"
        description="A prerequisite tree built from the API's blocked_by links. A blocked step cannot be completed."
      />

      <div className="card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem' }}>Plan towards a goal</h2>
        <form onSubmit={submitCreate}>
          <div className="field">
            <label htmlFor={goalId}>What do you want to be able to do?</label>
            <input
              id={goalId}
              type="text"
              value={goalText}
              onChange={(event) => setGoalText(event.target.value)}
              maxLength={500}
              required
            />
          </div>
          <div className="field">
            <label htmlFor={courseSelectId}>Course</label>
            <select
              id={courseSelectId}
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">All my material</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={hoursId}>Hours available per week</label>
            <input
              id={hoursId}
              type="number"
              min={0.5}
              max={168}
              step={0.5}
              value={hoursPerWeek}
              onChange={(event) => setHoursPerWeek(event.target.value)}
            />
            <span className="hint">Optional; used only to estimate how long the plan spans.</span>
          </div>
          <button type="submit" className="btn btn-primary" disabled={createRoadmap.isPending}>
            {createRoadmap.isPending ? 'Planning…' : 'Create roadmap'}
          </button>
        </form>
        <InlineError error={createRoadmap.error} />
      </div>

      {roadmaps.isPending ? (
        <SkeletonList rows={3} />
      ) : roadmaps.error !== null ? (
        <ErrorState
          error={roadmaps.error}
          title="Could not load your roadmaps"
          onRetry={() => {
            void roadmaps.refetch()
          }}
        />
      ) : roadmaps.data.length === 0 ? (
        <EmptyState
          title="No roadmaps yet"
          description="Describe a goal above and the planner will build an ordered prerequisite path."
        />
      ) : (
        <div className="stack">
          <div className="card">
            <div className="field" style={{ margin: 0 }}>
              <label htmlFor="roadmap-select">Roadmap</label>
              <select
                id="roadmap-select"
                value={effectiveId ?? ''}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {roadmaps.data.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.goal_text} (rev {item.revision}, {item.status})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {roadmap.isPending ? (
            <SkeletonList rows={4} />
          ) : roadmap.error !== null ? (
            <ErrorState
              error={roadmap.error}
              title="Could not load that roadmap"
              onRetry={() => {
                void roadmap.refetch()
              }}
            />
          ) : (
            <section className="card" aria-label="Roadmap steps">
              <div className="spread" style={{ marginBottom: '0.6rem' }}>
                <div>
                  <h2 style={{ fontSize: '1.05rem', marginBottom: '0.15rem' }}>
                    {roadmap.data.goal_text}
                  </h2>
                  <p className="row muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                    <Badge tone={roadmap.data.status === 'active' ? 'accent' : 'neutral'}>
                      {roadmap.data.status}
                    </Badge>
                    <span>revision {roadmap.data.revision}</span>
                    <span>{formatHours(roadmap.data.estimated_hours)} estimated</span>
                    <span>created {formatDateTime(roadmap.data.created_at)}</span>
                  </p>
                </div>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setAdaptOpen((current) => !current)}
                >
                  Adapt plan
                </button>
              </div>

              <p className="muted">{roadmap.data.reason}</p>

              {adaptOpen ? (
                <form
                  className="card"
                  style={{ marginBottom: '0.75rem' }}
                  onSubmit={(event) => {
                    event.preventDefault()
                    if (adaptReason.trim() === '' || effectiveId === null) return
                    adapt.mutate(
                      { roadmapId: effectiveId, payload: { reason: adaptReason.trim() } },
                      {
                        onSuccess: (next) => {
                          setAdaptReason('')
                          setAdaptOpen(false)
                          setSelectedId(next.id)
                        },
                      },
                    )
                  }}
                >
                  <div className="field">
                    <label htmlFor={adaptReasonId}>Why should the plan change?</label>
                    <textarea
                      id={adaptReasonId}
                      value={adaptReason}
                      onChange={(event) => setAdaptReason(event.target.value)}
                      maxLength={500}
                      required
                    />
                  </div>
                  <div className="row">
                    <button
                      type="submit"
                      className="btn btn-primary"
                      disabled={adapt.isPending}
                    >
                      {adapt.isPending ? 'Adapting…' : 'Request adaptation'}
                    </button>
                    <button type="button" className="btn" onClick={() => setAdaptOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : null}

              <InlineError error={adapt.error} />
              <InlineError error={updateStep.error} />

              <RoadmapTree
                roadmap={roadmap.data}
                recommendedStepId={recommendedStepId}
                busyStepId={busyStepId}
                onUpdateStep={handleUpdate}
              />
            </section>
          )}
        </div>
      )}
    </>
  )
}
