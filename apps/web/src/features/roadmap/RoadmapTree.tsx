import { useId, useState, type ReactNode } from 'react'

import type { RoadmapDetail, RoadmapStepStatus } from '@/api/types'
import { Badge } from '@/components/ui'
import { formatHours } from '@/lib/format'
import { stepVisualMeta, stepVisualState } from './status'
import { buildRoadmapTree, type RoadmapTreeNode } from './tree'

function StepNode({
  node,
  recommendedStepId,
  busyStepId,
  onUpdateStep,
}: {
  node: RoadmapTreeNode
  recommendedStepId: string | null
  busyStepId: string | null
  onUpdateStep: (stepId: string, status: Extract<RoadmapStepStatus, 'in_progress' | 'completed'>) => void
}): ReactNode {
  const [expanded, setExpanded] = useState(false)
  const panelId = useId()
  const { step } = node
  const isRecommended = step.id === recommendedStepId
  const visualState = stepVisualState(step.status, isRecommended)
  const meta = stepVisualMeta(visualState)
  const blocked = step.status === 'blocked'
  const busy = busyStepId === step.id

  return (
    <li className="roadmap-node">
      <div className="roadmap-card" data-status={visualState} data-step-id={step.id}>
        <button
          type="button"
          className="roadmap-toggle"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setExpanded((current) => !current)}
        >
          <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          <span className="roadmap-title">
            {step.order_index + 1}. {step.title}
          </span>
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </button>

        {expanded ? (
          <div id={panelId} style={{ marginTop: '0.5rem' }}>
            <p className="muted" style={{ margin: '0 0 0.4rem' }}>
              {step.description === '' ? meta.description : step.description}
            </p>
            <p className="muted" style={{ margin: '0 0 0.4rem', fontSize: '0.82rem' }}>
              Estimated {formatHours(step.estimated_hours)}
              {step.completed_at === null ? '' : ` · completed ${step.completed_at}`}
            </p>
            {blocked ? (
              <p className="banner banner-danger" role="note">
                <span aria-hidden="true">⛔</span>
                <span>
                  This step is blocked by {step.blocked_by.length}{' '}
                  {step.blocked_by.length === 1 ? 'prerequisite' : 'prerequisites'}. It cannot be
                  started or completed until they are.
                </span>
              </p>
            ) : null}
            <div className="row" role="group" aria-label={`Actions for ${step.title}`}>
              <button
                type="button"
                className="btn btn-sm"
                disabled={blocked || busy || step.status === 'in_progress' || step.status === 'completed'}
                onClick={() => onUpdateStep(step.id, 'in_progress')}
              >
                Mark in progress
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={blocked || busy || step.status === 'completed'}
                onClick={() => onUpdateStep(step.id, 'completed')}
              >
                {busy ? 'Saving…' : 'Mark complete'}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {node.children.length === 0 ? null : (
        <ul className="roadmap-children">
          {node.children.map((child) => (
            <StepNode
              key={child.step.id}
              node={child}
              recommendedStepId={recommendedStepId}
              busyStepId={busyStepId}
              onUpdateStep={onUpdateStep}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

export function RoadmapTree({
  roadmap,
  recommendedStepId,
  busyStepId = null,
  onUpdateStep,
}: {
  roadmap: RoadmapDetail
  recommendedStepId: string | null
  busyStepId?: string | null
  onUpdateStep: (stepId: string, status: Extract<RoadmapStepStatus, 'in_progress' | 'completed'>) => void
}): ReactNode {
  const nodes = buildRoadmapTree(roadmap.steps)

  return (
    <ul className="roadmap-tree" aria-label={`Steps for ${roadmap.goal_text}`}>
      {nodes.map((node) => (
        <StepNode
          key={node.step.id}
          node={node}
          recommendedStepId={recommendedStepId}
          busyStepId={busyStepId}
          onUpdateStep={onUpdateStep}
        />
      ))}
    </ul>
  )
}
