import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { PageHeader } from '@/components/ui'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/**
 * A real 404 page. The replaced client rendered nothing at all for an unknown
 * path, which looks like a crash.
 */
export function NotFoundPage(): ReactNode {
  useDocumentTitle('Page not found')
  return (
    <>
      <PageHeader
        title="Page not found"
        description="That address does not match any page in this workspace."
      />
      <div className="state">
        <span className="state-icon" aria-hidden="true">
          404
        </span>
        <p style={{ margin: 0 }}>
          <Link className="btn btn-primary" to="/dashboard">
            Back to the dashboard
          </Link>
        </p>
      </div>
    </>
  )
}
