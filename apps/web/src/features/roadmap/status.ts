import type { RoadmapStepStatus } from '@/api/types'
import type { Tone } from '@/components/ui'

export interface StepStatusMeta {
  label: string
  tone: Tone
  description: string
}

/**
 * The five step statuses the API defines, mapped to one visual language.
 *
 * "Recommended" is not a status: it is an overlay derived from the caller's
 * `next_action` (`ProgressSummaryResponse.next_action.step_id`), which is why it
 * is handled separately from this map.
 */
export const STEP_STATUS_META: Record<RoadmapStepStatus, StepStatusMeta> = {
  completed: {
    label: 'Completed',
    tone: 'success',
    description: 'You finished this step.',
  },
  in_progress: {
    label: 'In progress',
    tone: 'accent',
    description: 'You are working on this step now.',
  },
  available: {
    label: 'Available',
    tone: 'info',
    description: 'Its prerequisites are satisfied, so you can start it.',
  },
  pending: {
    label: 'Pending',
    tone: 'neutral',
    description: 'Not started yet.',
  },
  blocked: {
    label: 'Blocked',
    tone: 'danger',
    description: 'A prerequisite step is still incomplete.',
  },
}

export type StepVisualState = RoadmapStepStatus | 'recommended'

/** The visual state for a step, including the derived "recommended" overlay. */
export function stepVisualState(
  status: RoadmapStepStatus,
  isRecommended: boolean,
): StepVisualState {
  if (status === 'completed') return 'completed'
  return isRecommended ? 'recommended' : status
}

export function stepVisualMeta(state: StepVisualState): StepStatusMeta {
  if (state === 'recommended') {
    return {
      label: 'Recommended next',
      tone: 'accent',
      description: 'Your progress summary recommends this step next.',
    }
  }
  return STEP_STATUS_META[state]
}
