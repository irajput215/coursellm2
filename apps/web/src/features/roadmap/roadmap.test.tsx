import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeRoadmap, makeStep } from '@/test/handlers'
import { renderWithProviders } from '@/test/render'

import { RoadmapTree } from './RoadmapTree'

const STEPS = [
  makeStep({ id: 's-completed', concept_id: 'c-completed', order_index: 0, title: 'Completed step', status: 'completed' }),
  makeStep({ id: 's-progress', concept_id: 'c-progress', order_index: 1, title: 'In progress step', status: 'in_progress' }),
  makeStep({ id: 's-available', concept_id: 'c-available', order_index: 2, title: 'Available step', status: 'available' }),
  makeStep({ id: 's-pending', concept_id: 'c-pending', order_index: 3, title: 'Pending step', status: 'pending' }),
  makeStep({
    id: 's-blocked',
    concept_id: 'c-blocked',
    order_index: 4,
    title: 'Blocked step',
    status: 'blocked',
    blocked_by: ['c-available'],
  }),
]

function cardFor(stepId: string): HTMLElement {
  const card = document.querySelector(`[data-step-id="${stepId}"]`)
  if (!(card instanceof HTMLElement)) throw new Error(`No card rendered for step ${stepId}`)
  return card
}

describe('roadmap step states', () => {
  it('maps every API step status to its visual state', () => {
    renderWithProviders(
      <RoadmapTree roadmap={makeRoadmap(STEPS)} recommendedStepId={null} onUpdateStep={vi.fn()} />,
    )

    const expectations: [string, string, string][] = [
      ['s-completed', 'completed', 'Completed'],
      ['s-progress', 'in_progress', 'In progress'],
      ['s-available', 'available', 'Available'],
      ['s-pending', 'pending', 'Pending'],
      ['s-blocked', 'blocked', 'Blocked'],
    ]

    for (const [stepId, state, label] of expectations) {
      const card = cardFor(stepId)
      expect(card).toHaveAttribute('data-status', state)
      expect(within(card).getByText(label)).toBeInTheDocument()
    }
  })

  it('renders the recommended overlay from the progress next action', () => {
    renderWithProviders(
      <RoadmapTree
        roadmap={makeRoadmap(STEPS)}
        recommendedStepId="s-pending"
        onUpdateStep={vi.fn()}
      />,
    )

    const card = cardFor('s-pending')
    expect(card).toHaveAttribute('data-status', 'recommended')
    expect(within(card).getByText('Recommended next')).toBeInTheDocument()
  })

  it('does not let a blocked step be completed from the UI', async () => {
    const onUpdate = vi.fn()
    renderWithProviders(
      <RoadmapTree roadmap={makeRoadmap(STEPS)} recommendedStepId={null} onUpdateStep={onUpdate} />,
    )
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Blocked step/ }))

    const blockedCard = cardFor('s-blocked')
    const complete = within(blockedCard).getByRole('button', { name: 'Mark complete' })
    expect(complete).toBeDisabled()
    expect(within(blockedCard).getByRole('button', { name: 'Mark in progress' })).toBeDisabled()
    expect(within(blockedCard).getByText(/cannot be started or completed/)).toBeInTheDocument()

    await user.click(complete)
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('does let an available step be completed', async () => {
    const onUpdate = vi.fn()
    renderWithProviders(
      <RoadmapTree roadmap={makeRoadmap(STEPS)} recommendedStepId={null} onUpdateStep={onUpdate} />,
    )
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Available step/ }))
    await user.click(within(cardFor('s-available')).getByRole('button', { name: 'Mark complete' }))

    expect(onUpdate).toHaveBeenCalledWith('s-available', 'completed')
  })
})
