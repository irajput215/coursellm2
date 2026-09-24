import { isValidElement, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeToText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeToText(node.props.children)
  return ''
}

function detectLanguage(children: ReactNode): string {
  if (!isValidElement<{ className?: string }>(children)) return 'text'
  const className = children.props.className ?? ''
  const match = /(?:language|lang)-([\w+#.-]+)/u.exec(className)
  return match?.[1] ?? 'text'
}

/** A fenced code block with a language label and a copy button. */
export function CodeBlock({ children }: { children?: ReactNode }): ReactNode {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const language = detectLanguage(children)
  const source = nodeToText(children).replace(/\n$/u, '')

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback(() => {
    const clipboard = navigator.clipboard
    if (clipboard === undefined) return
    void clipboard
      .writeText(source)
      .then(() => {
        setCopied(true)
        if (timer.current !== null) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => undefined)
  }, [source])

  return (
    <div className="code-block">
      <div className="code-block-head">
        <span className="code-language">{language}</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={copy}
          aria-label={`Copy ${language} code block`}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  )
}

function PreRenderer({ children }: { children?: ReactNode }): ReactNode {
  return <CodeBlock>{children}</CodeBlock>
}

function LinkRenderer({
  href,
  children,
  node: _node,
  ...rest
}: {
  href?: string | undefined
  children?: ReactNode
  node?: unknown
}): ReactNode {
  const external = href !== undefined && /^https?:\/\//u.test(href)
  return (
    <a
      href={href}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      {...rest}
    >
      {children}
    </a>
  )
}

/**
 * Markdown with GFM (tables, task lists, strikethrough) and highlighted code.
 */
export function Markdown({ content }: { content: string }): ReactNode {
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{ pre: PreRenderer, a: LinkRenderer }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
