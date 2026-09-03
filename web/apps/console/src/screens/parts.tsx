import type { JSX, ReactNode } from 'react';
import { useState } from 'react';
import { Button, type ProblemSummary } from '@draupnir/jarngreipr';

/**
 * The two things every screen has, so that no screen invents its own.
 *
 * A page heading that receives focus on navigation (UX 11), and an error
 * surface that shows the title, the available action and a copyable
 * correlation identifier (AC-U14). Both are here rather than repeated because
 * twenty-nine slightly different error panels is how a criterion like AC-U14
 * ends up true of most screens.
 */

export interface PageHeadingProps {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}

export function PageHeading({ title, subtitle, action }: PageHeadingProps): JSX.Element {
  return (
    <header className="cn-page">
      <div>
        {/* `tabIndex={-1}` so the shell can move focus here after a navigation.
            Not in the tab order: it is a target, not a stop. */}
        <h1 tabIndex={-1}>{title}</h1>
        {subtitle === undefined ? null : <p className="cn-page__subtitle">{subtitle}</p>}
      </div>
      {action === undefined ? null : <div className="cn-page__action">{action}</div>}
    </header>
  );
}

export interface ErrorSurfaceProps {
  problem: ProblemSummary;
  /** What the operator can do about it. Rendered as a control, not as prose. */
  action?: { label: string; onSelect: () => void };
}

/**
 * AC-U14: "Every error surface shows the problem title, the available action
 * and a copyable correlation identifier."
 *
 * The identifier is selectable and has a copy control, because "quote this
 * when you report it" needs something to quote and transcribing a UUID by hand
 * from a screen is how the wrong one ends up in the ticket.
 */
export function ErrorSurface({ problem, action }: ErrorSurfaceProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  return (
    <section className="cn-error" role="alert" aria-labelledby="cn-error-title">
      <h2 className="cn-error__title" id="cn-error-title">
        {problem.title}
      </h2>
      {problem.detail === undefined ? null : <p className="cn-error__detail">{problem.detail}</p>}

      <div className="cn-error__actions">
        {action === undefined ? (
          <p className="cn-error__no-action">
            There is nothing to retry here. If this persists, report it with the identifier below.
          </p>
        ) : (
          <Button onClick={action.onSelect}>{action.label}</Button>
        )}
      </div>

      {problem.correlationId === undefined ? null : (
        <p className="cn-error__correlation">
          <span className="cn-error__correlation-label">Correlation identifier</span>
          <code data-testid="correlation-id">{problem.correlationId}</code>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              void navigator.clipboard
                .writeText(problem.correlationId ?? '')
                .then(() => {
                  setCopied(true);
                })
                .catch(() => {
                  // Clipboard access can be refused. The identifier is still
                  // selectable, so saying nothing is better than claiming a
                  // copy that did not happen.
                  setCopied(false);
                });
            }}
          >
            Copy
          </Button>
          <span role="status" className="cn-error__copied">
            {copied ? 'Copied.' : ''}
          </span>
        </p>
      )}
    </section>
  );
}
