import type { RoadmapStep } from '@/api/types'

export interface RoadmapTreeNode {
  step: RoadmapStep
  children: RoadmapTreeNode[]
}

function byOrder(a: RoadmapStep, b: RoadmapStep): number {
  return a.order_index - b.order_index
}

/**
 * A prerequisite tree built from `blocked_by`.
 *
 * `blocked_by` holds concept ids, so a blocker is resolved to the step that
 * teaches that concept. A step that no other step requires is a root. Cycles
 * (which the planner should never emit) are broken by a visited set rather than
 * recursing forever.
 */
export function buildRoadmapTree(steps: readonly RoadmapStep[]): RoadmapTreeNode[] {
  const byConcept = new Map<string, RoadmapStep>()
  for (const step of steps) {
    if (step.concept_id !== null) byConcept.set(step.concept_id, step)
  }

  const childrenOf = new Map<string, RoadmapStep[]>()
  const hasParent = new Set<string>()
  for (const step of steps) {
    for (const blockerConceptId of step.blocked_by) {
      const parent = byConcept.get(blockerConceptId)
      if (parent === undefined || parent.id === step.id) continue
      const siblings = childrenOf.get(parent.id) ?? []
      siblings.push(step)
      childrenOf.set(parent.id, siblings)
      hasParent.add(step.id)
      break
    }
  }

  const visited = new Set<string>()
  const build = (step: RoadmapStep): RoadmapTreeNode => {
    if (visited.has(step.id)) return { step, children: [] }
    visited.add(step.id)
    const children = (childrenOf.get(step.id) ?? []).slice().sort(byOrder).map(build)
    return { step, children }
  }

  return steps
    .filter((step) => !hasParent.has(step.id))
    .slice()
    .sort(byOrder)
    .map(build)
}
