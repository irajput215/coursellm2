import type { ChatTurn } from './useChatStream'

export interface DerivedAction {
  id: string
  label: string
  build: (question: string) => string
}

/**
 * The action row under an answer. Each entry derives a new prompt from the
 * question the student actually asked — nothing here is canned content.
 */
export const DERIVED_ACTIONS: DerivedAction[] = [
  {
    id: 'simpler',
    label: 'Explain simpler',
    build: (question) =>
      `Explain your previous answer in simpler terms, as if I were new to the topic. Original question: "${question}"`,
  },
  {
    id: 'example',
    label: 'Give an example',
    build: (question) =>
      `Give a concrete worked example that illustrates your previous answer. Original question: "${question}"`,
  },
  {
    id: 'quiz',
    label: 'Quiz me',
    build: (question) =>
      `Ask me one question that tests whether I understood your previous answer. Wait for my answer before marking it. Original question: "${question}"`,
  },
  {
    id: 'deeper',
    label: 'Go deeper',
    build: (question) =>
      `Go deeper on your previous answer: the underlying mechanism, the assumptions it rests on, and the edge cases where it breaks down. Original question: "${question}"`,
  },
]

/**
 * Follow-up suggestions derived from the citations the API returned.
 *
 * If an answer carried no citations there is nothing to suggest from, so the
 * UI suggests nothing rather than inventing a question.
 */
export function suggestedFollowUps(turn: ChatTurn): string[] {
  const filenames = [...new Set(turn.citations.map((citation) => citation.filename))]
  const first = filenames[0]
  if (first === undefined) return []
  const suggestions = [`What does "${first}" say about this?`]
  const second = filenames[1]
  if (second !== undefined) {
    suggestions.push(`Do the sources agree? Compare "${first}" and "${second}".`)
  }
  return suggestions
}

/** The question that produced an assistant turn, if it can be determined. */
export function questionForTurn(turns: readonly ChatTurn[], turnIndex: number): string {
  const turn = turns[turnIndex]
  if (turn === undefined) return ''
  if (turn.question !== '') return turn.question
  for (let index = turnIndex - 1; index >= 0; index -= 1) {
    const candidate = turns[index]
    if (candidate !== undefined && candidate.role === 'user') return candidate.content
  }
  return ''
}
