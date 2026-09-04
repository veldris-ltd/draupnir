import type { JSX, ReactNode } from 'react';
import './states.css';

/**
 * The six states every JARNGREIPR component ships.
 *
 * AC-U2, and the prompt puts it more sharply: "a component with only a happy
 * path is not done".
 *
 * The reason this is one shared contract rather than six props per component
 * is that a convention followed component by component is a convention the
 * twentieth component skips. Here a component takes one `state` and renders
 * its content only in `ready`; the other five are rendered by `StateSurface`
 * from one implementation, so they cannot drift apart and cannot be forgotten.
 *
 * The five non-ready states are not decoration. Each one is a distinct thing
 * an operator needs to be told, and collapsing any two of them loses
 * information they act on:
 *
 * - `loading`   the answer is coming. Wait.
 * - `empty`     there is genuinely nothing. Not an error; do not retry.
 * - `error`     something failed. The problem document says what and why.
 * - `denied`    your role does not permit this. Ask an administrator, do not
 *               retry, and do not read the absence as "nothing here".
 * - `readOnly`  you may see it and not change it. The controls are visible
 *               and disabled rather than hidden, so the shape of what exists
 *               is still legible.
 * - `partitioned` the site is cut off from the federation. Training continues
 *               and release is blocked (Decision S8). This is emphatically not
 *               an error: it is a normal operating condition with a
 *               consequence, and rendering it as a failure sends an operator
 *               to investigate a network they cannot fix.
 */
export type ComponentState =
  'ready' | 'loading' | 'empty' | 'error' | 'denied' | 'readOnly' | 'partitioned';

/** The five states that replace a component's content, in story order. */
export const REPLACING_STATES = [
  'loading',
  'empty',
  'error',
  'denied',
  'partitioned',
] as const satisfies readonly ComponentState[];

/**
 * Every state a component ships a story for. AC-U2 names six; `ready` is the
 * seventh because a happy path still needs a snapshot.
 */
export const ALL_STATES = [
  'ready',
  'loading',
  'empty',
  'error',
  'denied',
  'readOnly',
  'partitioned',
] as const satisfies readonly ComponentState[];

/** What a component needs to render any of its states. */
export interface StateProps {
  /** Which state to render. Defaults to `ready`. */
  state?: ComponentState | undefined;
  /** Overrides the default wording for the current state. */
  stateMessage?: string | undefined;
  /**
   * The RFC 9457 problem document behind an `error`, so the component can show
   * the type title, what to do, and a copyable correlation identifier
   * (SAD 11F.3).
   */
  problem?: ProblemSummary | undefined;
}

/** The parts of an RFC 9457 problem document a component renders. */
export interface ProblemSummary {
  title: string;
  detail?: string | undefined;
  code?: string | undefined;
  /** Copyable, because "quote this when you report it" needs something to quote. */
  correlationId?: string | undefined;
}

/** Whether this state replaces the component's content entirely. */
export function replacesContent(state: ComponentState): boolean {
  return state !== 'ready' && state !== 'readOnly';
}

/** Whether this state disables every control the component owns. */
export function isInert(state: ComponentState): boolean {
  return state !== 'ready';
}

/**
 * The default wording per state.
 *
 * Plain sentences rather than labels. SAD 11F.3 asks for the consequence
 * stated in words, and "Partitioned" on its own tells an operator nothing
 * about what they can still do.
 */
const WORDING: Record<Exclude<ComponentState, 'ready'>, { title: string; detail: string }> = {
  loading: { title: 'Loading', detail: 'Fetching the current state.' },
  empty: { title: 'Nothing to show', detail: 'There is nothing here yet.' },
  error: {
    title: 'Something went wrong',
    detail: 'The request did not complete. Quote the correlation identifier when reporting it.',
  },
  denied: {
    title: 'Not permitted',
    detail:
      'Your role does not permit this. This is not an empty result: there may be data here that you cannot see.',
  },
  readOnly: {
    title: 'Read only',
    detail: 'You can see this and not change it.',
  },
  partitioned: {
    title: 'Site partitioned from the federation',
    detail:
      'Training and evaluation continue. Release is unavailable until the link returns and the chain head is countersigned.',
  },
};

/** The icon glyph per state. Decorative: the wording carries the meaning. */
const GLYPH: Record<Exclude<ComponentState, 'ready'>, string> = {
  loading: '',
  empty: '—',
  error: '!',
  denied: '⦸',
  readOnly: '🔒',
  partitioned: '⇸',
};

/** The four heights a replacing state may hold. See `reserve`. */
export type Reserve = 'sm' | 'md' | 'lg' | 'xl';

export interface StateSurfaceProps extends StateProps {
  /** What this component is, for the accessible label. */
  label: string;
  /** Rendered when the state is `ready` or `readOnly`. */
  children: ReactNode;
  /**
   * How much room to hold while a replacing state is shown, so the layout does
   * not collapse and then jump when the content arrives.
   *
   * A name from a small set rather than a length, because a length here is a
   * visual value in a component and prompt UX-2 rules those out: every one of
   * these resolves to a multiple of the space scale in `states.css`. Four
   * sizes cover everything the system has -- a toast, a card, a table, a log.
   */
  reserve?: Reserve | undefined;
}

/**
 * Renders the five replacing states, or the component's own content.
 *
 * The live region is the accessibility half of AC-U2. SAD 11F.4 requires live
 * regions to announce state changes, and a state swap is exactly that: a
 * screen reader user who is not told the panel became `denied` reads an empty
 * region and concludes there is nothing there, which is the one wrong
 * conclusion available.
 *
 * `polite` rather than `assertive`, because a run board with fifty six panels
 * settling at once would otherwise interrupt continuously.
 */
export function StateSurface({
  state = 'ready',
  stateMessage,
  problem,
  label,
  children,
  reserve,
}: StateSurfaceProps): JSX.Element {
  if (state === 'ready' || state === 'readOnly') {
    return (
      <>
        {state === 'readOnly' ? (
          <p className="jg-state__banner" data-jg-state="readOnly">
            <span aria-hidden="true">{GLYPH.readOnly}</span>
            <span>{stateMessage ?? WORDING.readOnly.detail}</span>
          </p>
        ) : null}
        {children}
      </>
    );
  }

  const wording = WORDING[state];
  const title = state === 'error' && problem ? problem.title : wording.title;
  const detail =
    stateMessage ?? (state === 'error' ? (problem?.detail ?? wording.detail) : wording.detail);

  return (
    <div
      className="jg-state"
      data-jg-state={state}
      data-jg-reserve={reserve}
      role="status"
      aria-live="polite"
      aria-label={`${label}: ${title}`}
    >
      {state === 'loading' ? (
        <span className="jg-state__spinner" aria-hidden="true" />
      ) : (
        <span className="jg-state__glyph" aria-hidden="true">
          {GLYPH[state]}
        </span>
      )}
      <p className="jg-state__title">{title}</p>
      <p className="jg-state__detail">{detail}</p>
      {state === 'error' && problem?.correlationId !== undefined ? (
        <p className="jg-state__correlation">
          <span className="jg-sr-only">Correlation identifier, quote when reporting: </span>
          <code>{problem.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
