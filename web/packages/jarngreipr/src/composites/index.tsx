import type { JSX, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useCallback, useId, useMemo, useRef, useState } from 'react';
import { StateSurface, type StateProps } from '../state/states';
import { Badge, Button, type Tone } from '../primitives';
import './composites.css';

/**
 * The eight composites of SAD 11F.1.
 *
 * A composite is where the design system stops being generic and starts
 * knowing what DRAUPNIR is: a run has a lifecycle state, a gate has evidence
 * with digests, a ledger entry has a predecessor. Putting that knowledge here
 * rather than in the screens is what makes the six states enforceable -- a
 * screen that assembles its own gate card out of divs ships a happy path and
 * nothing else, and nobody notices until an operator meets a denial.
 *
 * Each one takes the same `StateProps` as a primitive and delegates its five
 * replacing states to `StateSurface`, so `state="partitioned"` on a run card
 * says exactly what it says on a button.
 */

// ---------------------------------------------------------------------------
// Run card
// ---------------------------------------------------------------------------

/** The run lifecycle of SAD 6.1. */
export type RunState =
  | 'DRAFT'
  | 'QUEUED'
  | 'RUNNING'
  | 'TRAINED'
  | 'EVALUATED'
  | 'GATED'
  | 'RELEASED'
  | 'FAILED'
  | 'CANCELLED';

/** Which tone each lifecycle state reads as. */
const RUN_TONE: Record<RunState, Tone> = {
  DRAFT: 'neutral',
  QUEUED: 'neutral',
  RUNNING: 'info',
  TRAINED: 'info',
  EVALUATED: 'info',
  GATED: 'warning',
  RELEASED: 'success',
  FAILED: 'danger',
  CANCELLED: 'neutral',
};

export interface RunAction {
  label: string;
  onSelect?: (() => void) | undefined;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | undefined;
}

export interface RunCardProps extends StateProps {
  runId: string;
  /** Which CIM this run is producing. */
  model: string;
  runState: RunState;
  /** Steps completed and steps planned, for the progress bar. */
  step?: number | undefined;
  totalSteps?: number | undefined;
  startedAt?: string | undefined;
  /** Who submitted it. Custody matters (SAD 16A), so it is always on the face. */
  submittedBy?: string | undefined;
  actions?: RunAction[] | undefined;
}

/**
 * One run, at a glance.
 *
 * The progress bar is a `progressbar` with a `valuetext`, not a bare div: the
 * percentage is the least useful number on it, and "step 4,200 of 12,000" is
 * what an operator actually reads.
 */
export function RunCard({
  runId,
  model,
  runState,
  step,
  totalSteps,
  startedAt,
  submittedBy,
  actions = [],
  state = 'ready',
  stateMessage,
  problem,
}: RunCardProps): JSX.Element {
  const headingId = useId();
  const hasProgress = step !== undefined && totalSteps !== undefined && totalSteps > 0;
  const pct = hasProgress ? Math.min(100, Math.round((step / totalSteps) * 100)) : 0;

  return (
    <section className="jg-card" aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={`Run ${runId}`}
        minHeight="10rem"
      >
        <div className="jg-card__head">
          <div>
            <h3 className="jg-card__title" id={headingId}>
              {model}
            </h3>
            <p className="jg-card__subtitle">{runId}</p>
          </div>
          <Badge tone={RUN_TONE[runState]}>{runState}</Badge>
        </div>

        <dl className="jg-facts">
          {startedAt === undefined ? null : (
            <>
              <dt>Started</dt>
              <dd>{startedAt}</dd>
            </>
          )}
          {submittedBy === undefined ? null : (
            <>
              <dt>Submitted by</dt>
              <dd>{submittedBy}</dd>
            </>
          )}
        </dl>

        {hasProgress ? (
          <div className="jg-run__progress">
            <div
              className="jg-run__track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={totalSteps}
              aria-valuenow={step}
              aria-valuetext={`Step ${step.toLocaleString()} of ${totalSteps.toLocaleString()}`}
              aria-label={`Training progress for run ${runId}`}
            >
              <div className="jg-run__fill" style={{ width: `${String(pct)}%` }} />
            </div>
            <p className="jg-run__legend">
              <span>
                Step {step.toLocaleString()} of {totalSteps.toLocaleString()}
              </span>
              <span>{pct}%</span>
            </p>
          </div>
        ) : null}

        {actions.length === 0 ? null : (
          <div className="jg-card__actions">
            {actions.map((action) => (
              <Button
                key={action.label}
                variant={action.variant ?? 'secondary'}
                size="sm"
                state={state === 'ready' ? 'ready' : 'readOnly'}
                onClick={action.onSelect}
              >
                {action.label}
              </Button>
            ))}
          </div>
        )}
      </StateSurface>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Gate card with evidence
// ---------------------------------------------------------------------------

export type GateDecision = 'allow' | 'deny' | 'waived';

/** One piece of evidence a gate rule was judged against (SAD 5.2, GLEIPNIR). */
export interface GateEvidence {
  /** What the evidence is: `eval-report`, `card`, `scan`, `attestation`. */
  kind: string;
  /** The rule clause this evidence satisfies or fails. */
  requirement: string;
  met: boolean;
  /** The SHA-256 content digest. Selectable, because it gets pasted into tickets. */
  digest: string;
  observed?: string | undefined;
}

export interface GateCardProps extends StateProps {
  gate: string;
  decision: GateDecision;
  /** The policy bundle version the decision was taken under. */
  policyVersion?: string | undefined;
  /** Who waived it, when the decision is `waived`. Never blank on a waiver. */
  waivedBy?: string | undefined;
  waiverReason?: string | undefined;
  evidence: GateEvidence[];
}

const DECISION_GLYPH: Record<GateDecision, string> = {
  allow: '✓',
  deny: '✕',
  waived: '!',
};

const DECISION_WORDS: Record<GateDecision, string> = {
  allow: 'Allowed',
  deny: 'Denied',
  waived: 'Waived',
};

const DECISION_TONE: Record<GateDecision, Tone> = {
  allow: 'success',
  deny: 'danger',
  waived: 'warning',
};

/**
 * A gate decision with the evidence behind it.
 *
 * The evidence is not an appendix. Decision S4 makes GLEIPNIR the judge and
 * HODD the record, which means a decision an operator cannot trace back to
 * digests is a decision they have to take on trust -- and a waiver with no
 * named waiver is exactly the custody failure SAD 16A is about. So the digest
 * of every artefact is on the face of the card, and a waiver renders who and
 * why or it does not render as waived.
 */
export function GateCard({
  gate,
  decision,
  policyVersion,
  waivedBy,
  waiverReason,
  evidence,
  state = 'ready',
  stateMessage,
  problem,
}: GateCardProps): JSX.Element {
  const headingId = useId();
  const shown = state === 'empty' ? [] : evidence;

  return (
    <section className="jg-card jg-gate" data-jg-decision={decision} aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={`Gate ${gate}`}
        minHeight="12rem"
      >
        <div className="jg-card__head">
          <div>
            <h3 className="jg-card__title" id={headingId}>
              {gate}
            </h3>
            {policyVersion === undefined ? null : (
              <p className="jg-card__subtitle">policy {policyVersion}</p>
            )}
          </div>
          <Badge tone={DECISION_TONE[decision]}>{DECISION_WORDS[decision]}</Badge>
        </div>

        {/*
         * The verdict repeats the badge in a sentence rather than a label. The
         * left border is colour, the badge is colour plus a word, and this is
         * the word on its own -- three carriers, because AC-U7 does not accept
         * one.
         */}
        <p className="jg-gate__verdict">
          <span aria-hidden="true">{DECISION_GLYPH[decision]}</span>
          <span>
            {decision === 'allow'
              ? 'Every requirement was met.'
              : decision === 'deny'
                ? 'At least one requirement was not met. Release is blocked.'
                : 'Requirements were not met and the gate was waived by a named approver.'}
          </span>
        </p>

        {decision === 'waived' ? (
          <dl className="jg-facts">
            <dt>Waived by</dt>
            <dd>{waivedBy ?? 'Unrecorded — this is a defect, not a blank field'}</dd>
            <dt>Reason</dt>
            <dd>{waiverReason ?? 'Unrecorded — this is a defect, not a blank field'}</dd>
          </dl>
        ) : null}

        {shown.length === 0 ? (
          <p className="jg-gate__verdict">
            <span>No evidence was recorded against this gate.</span>
          </p>
        ) : (
          <ul className="jg-gate__evidence" aria-label={`Evidence for ${gate}`}>
            {shown.map((item) => (
              <li
                className="jg-gate__item"
                key={`${item.kind}:${item.digest}`}
                data-jg-met={String(item.met)}
              >
                <span className="jg-gate__mark" aria-hidden="true">
                  {item.met ? '✓' : '✕'}
                </span>
                <span>
                  <span className="jg-sr-only">{item.met ? 'Met: ' : 'Not met: '}</span>
                  {item.requirement}
                  {item.observed === undefined ? null : ` — observed ${item.observed}`}
                </span>
                <span className="jg-gate__digest">
                  <span className="jg-sr-only">{item.kind} digest: </span>
                  {item.digest}
                </span>
              </li>
            ))}
          </ul>
        )}
      </StateSurface>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Lineage tree
// ---------------------------------------------------------------------------

export interface LineageNode {
  id: string;
  label: string;
  /** `corpus`, `checkpoint`, `eval`, `release`: what kind of artefact this is. */
  kind: string;
  digest?: string | undefined;
  children?: LineageNode[] | undefined;
}

export interface LineageTreeProps extends StateProps {
  label: string;
  roots: LineageNode[];
  selectedId?: string | undefined;
  onSelect?: ((id: string) => void) | undefined;
}

/**
 * The provenance of an artefact, as a tree.
 *
 * A real `role="tree"` with roving tabindex rather than nested details
 * elements, because SAD 11F.4 asks for keyboard operation and a lineage six
 * levels deep is where "tab through everything" stops being usable. Arrow keys
 * move and expand, Home and End jump to the ends, exactly as the ARIA
 * authoring practices specify -- an operator who knows one tree knows this one.
 */
export function LineageTree({
  label,
  roots,
  selectedId,
  onSelect,
  state = 'ready',
  stateMessage,
  problem,
}: LineageTreeProps): JSX.Element {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(collectIds(roots)));
  const [focusId, setFocusId] = useState<string | undefined>(() => roots[0]?.id);
  const treeRef = useRef<HTMLUListElement>(null);

  const visible = useMemo(() => flatten(roots, expanded), [roots, expanded]);
  const current = focusId ?? visible[0]?.node.id;

  const move = useCallback((id: string) => {
    setFocusId(id);
    treeRef.current?.querySelector<HTMLElement>(`[data-jg-node="${id}"]`)?.focus();
  }, []);

  const toggle = useCallback((id: string, open: boolean) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  function onKeyDown(event: ReactKeyboardEvent<HTMLUListElement>): void {
    if (current === undefined) return;
    const index = visible.findIndex((row) => row.node.id === current);
    if (index < 0) return;
    const row = visible[index];
    if (row === undefined) return;
    const hasChildren = (row.node.children ?? []).length > 0;
    const open = expanded.has(row.node.id);

    switch (event.key) {
      case 'ArrowDown': {
        const next = visible[index + 1];
        if (next) move(next.node.id);
        break;
      }
      case 'ArrowUp': {
        const previous = visible[index - 1];
        if (previous) move(previous.node.id);
        break;
      }
      case 'ArrowRight': {
        if (hasChildren && !open) toggle(row.node.id, true);
        else if (hasChildren) {
          const next = visible[index + 1];
          if (next) move(next.node.id);
        }
        break;
      }
      case 'ArrowLeft': {
        if (hasChildren && open) toggle(row.node.id, false);
        else if (row.parentId !== undefined) move(row.parentId);
        break;
      }
      case 'Home': {
        const first = visible[0];
        if (first) move(first.node.id);
        break;
      }
      case 'End': {
        const last = visible[visible.length - 1];
        if (last) move(last.node.id);
        break;
      }
      case 'Enter':
      case ' ': {
        onSelect?.(row.node.id);
        break;
      }
      default:
        return;
    }
    event.preventDefault();
  }

  function renderNodes(nodes: LineageNode[], level: number): JSX.Element {
    return (
      <ul
        className={level === 1 ? 'jg-tree__root' : undefined}
        role={level === 1 ? 'tree' : 'group'}
        aria-label={level === 1 ? label : undefined}
        aria-multiselectable={level === 1 ? false : undefined}
        ref={level === 1 ? treeRef : undefined}
        onKeyDown={level === 1 ? onKeyDown : undefined}
      >
        {nodes.map((node, index) => {
          const children = node.children ?? [];
          const open = expanded.has(node.id);
          return (
            <li
              key={node.id}
              role="treeitem"
              aria-expanded={children.length > 0 ? open : undefined}
              aria-selected={node.id === selectedId}
              aria-level={level}
              aria-setsize={nodes.length}
              aria-posinset={index + 1}
            >
              <button
                type="button"
                className="jg-tree__row"
                data-jg-node={node.id}
                tabIndex={node.id === current ? 0 : -1}
                onFocus={() => {
                  setFocusId(node.id);
                }}
                onClick={() => {
                  if (children.length > 0) toggle(node.id, !open);
                  onSelect?.(node.id);
                }}
              >
                <span className="jg-tree__twisty" aria-hidden="true">
                  {children.length > 0 ? '▸' : '·'}
                </span>
                <span className="jg-tree__kind">{node.kind}</span>
                <span>{node.label}</span>
                {node.digest === undefined ? null : (
                  <span className="jg-tree__digest">
                    <span className="jg-sr-only">digest </span>
                    {node.digest.slice(0, 12)}…
                  </span>
                )}
              </button>
              {children.length > 0 && open ? renderNodes(children, level + 1) : null}
            </li>
          );
        })}
      </ul>
    );
  }

  const shown = state === 'empty' ? [] : roots;

  return (
    <div className="jg-tree">
      <StateSurface
        state={shown.length === 0 && state === 'ready' ? 'empty' : state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        minHeight="10rem"
      >
        {renderNodes(shown, 1)}
      </StateSurface>
    </div>
  );
}

interface FlatRow {
  node: LineageNode;
  parentId?: string | undefined;
}

function flatten(
  nodes: LineageNode[],
  expanded: ReadonlySet<string>,
  parentId?: string,
): FlatRow[] {
  const rows: FlatRow[] = [];
  for (const node of nodes) {
    rows.push({ node, parentId });
    const children = node.children ?? [];
    if (children.length > 0 && expanded.has(node.id)) {
      rows.push(...flatten(children, expanded, node.id));
    }
  }
  return rows;
}

function collectIds(nodes: LineageNode[]): string[] {
  return nodes.flatMap((node) => [node.id, ...collectIds(node.children ?? [])]);
}

// ---------------------------------------------------------------------------
// Sweep comparison matrix
// ---------------------------------------------------------------------------

export interface SweepMetric {
  key: string;
  label: string;
  /** Whether a larger number is better. Drives which cell is marked best. */
  higherIsBetter: boolean;
  unit?: string | undefined;
}

export interface SweepArm {
  id: string;
  label: string;
  /** Metric key to value. A missing metric renders as "not measured". */
  values: Record<string, number | undefined>;
}

export interface SweepMatrixProps extends StateProps {
  caption: string;
  metrics: SweepMetric[];
  arms: SweepArm[];
}

/**
 * Every arm of a sweep against every metric, with the best cell marked.
 *
 * The best cell carries a check glyph and a screen-reader word as well as the
 * green wash, because "the green one" is not a thing a colour-blind operator
 * can act on. Ties mark every tied cell rather than the first: silently
 * picking a winner out of a tie is how a sweep report becomes wrong.
 */
export function SweepMatrix({
  caption,
  metrics,
  arms,
  state = 'ready',
  stateMessage,
  problem,
}: SweepMatrixProps): JSX.Element {
  const shown = useMemo(() => (state === 'empty' ? [] : arms), [state, arms]);

  const best = useMemo(() => {
    const result = new Map<string, number>();
    for (const metric of metrics) {
      const values = shown
        .map((arm) => arm.values[metric.key])
        .filter((value): value is number => value !== undefined);
      if (values.length === 0) continue;
      result.set(metric.key, metric.higherIsBetter ? Math.max(...values) : Math.min(...values));
    }
    return result;
  }, [metrics, shown]);

  return (
    <StateSurface
      state={shown.length === 0 && state === 'ready' ? 'empty' : state}
      stateMessage={stateMessage}
      problem={problem}
      label={caption}
      minHeight="12rem"
    >
      <div className="jg-matrix-wrap">
        <table className="jg-matrix">
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Arm</th>
              {metrics.map((metric) => (
                <th scope="col" key={metric.key}>
                  {metric.label}
                  {metric.unit === undefined ? null : ` (${metric.unit})`}
                  <span className="jg-sr-only">
                    , {metric.higherIsBetter ? 'higher is better' : 'lower is better'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((arm) => (
              <tr key={arm.id}>
                <th scope="row">{arm.label}</th>
                {metrics.map((metric) => {
                  const value = arm.values[metric.key];
                  const isBest = value !== undefined && best.get(metric.key) === value;
                  return (
                    <td
                      key={metric.key}
                      data-jg-numeric="true"
                      data-jg-best={isBest ? 'true' : undefined}
                    >
                      {value === undefined ? (
                        <span aria-label="Not measured">—</span>
                      ) : (
                        <>
                          {isBest ? (
                            <>
                              <span aria-hidden="true">✓ </span>
                              <span className="jg-sr-only">Best: </span>
                            </>
                          ) : null}
                          {value.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                        </>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </StateSurface>
  );
}

// ---------------------------------------------------------------------------
// Virtualised log viewer
// ---------------------------------------------------------------------------

export interface LogLine {
  /** One-based line number as it appears in the stored log. */
  number: number;
  text: string;
  level?: 'info' | 'warning' | 'error' | undefined;
}

export interface LogViewerProps extends StateProps {
  label: string;
  lines: LogLine[];
  /** Whether the tail is still being written. Drives the live region. */
  streaming?: boolean | undefined;
}

/** Pixel height of one log row. Mirrors `.jg-log__line` in the stylesheet. */
const LOG_ROW_HEIGHT = 20;
/** Rows rendered outside the viewport, so a fast scroll does not show gaps. */
const LOG_OVERSCAN = 10;

/**
 * A log viewer that renders only what is on screen.
 *
 * A training log runs to millions of lines and a browser asked to lay all of
 * them out stops responding, which in practice means an operator cannot read
 * the log of the run that is going wrong. So the viewport is a fixed height,
 * a spacer holds the full scroll extent, and only the visible window plus an
 * overscan is in the DOM.
 *
 * The accessibility cost of virtualisation is real and is paid explicitly:
 * a screen reader cannot read rows that do not exist, so the region is
 * labelled with the total line count and the visible range, and the tail
 * announcement is `polite` and only while streaming.
 */
export function LogViewer({
  label,
  lines,
  streaming = false,
  state = 'ready',
  stateMessage,
  problem,
}: LogViewerProps): JSX.Element {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(320);
  const shown = state === 'empty' ? [] : lines;

  const first = Math.max(0, Math.floor(scrollTop / LOG_ROW_HEIGHT) - LOG_OVERSCAN);
  const count = Math.ceil(viewportHeight / LOG_ROW_HEIGHT) + LOG_OVERSCAN * 2;
  const window = shown.slice(first, first + count);
  const last = shown[shown.length - 1];

  return (
    <div className="jg-log">
      <StateSurface
        state={shown.length === 0 && state === 'ready' ? 'empty' : state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        minHeight="20rem"
      >
        <div className="jg-log__bar">
          <span>{label}</span>
          <span>
            {shown.length.toLocaleString()} lines
            {streaming ? ', still writing' : ''}
          </span>
        </div>
        <div
          className="jg-log__viewport"
          // Focusable because it scrolls: WCAG 2.1.1 requires a keyboard
          // user to be able to reach and scroll this region, and jsx-a11y
          // cannot see that it overflows.
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          role="log"
          aria-label={`${label}, ${shown.length.toLocaleString()} lines, showing ${String(first + 1)} to ${String(Math.min(shown.length, first + window.length))}`}
          onScroll={(event) => {
            setScrollTop(event.currentTarget.scrollTop);
            setViewportHeight(event.currentTarget.clientHeight);
          }}
        >
          <div
            className="jg-log__spacer"
            style={{ height: `${String(shown.length * LOG_ROW_HEIGHT)}px` }}
          >
            <div
              className="jg-log__window"
              style={{ transform: `translateY(${String(first * LOG_ROW_HEIGHT)}px)` }}
            >
              {window.map((line) => (
                <div
                  className="jg-log__line"
                  key={line.number}
                  data-jg-level={line.level ?? 'info'}
                >
                  <span className="jg-log__lineno" aria-hidden="true">
                    {line.number}
                  </span>
                  <span className="jg-log__text">
                    {line.level === undefined || line.level === 'info' ? null : (
                      <span className="jg-sr-only">{line.level}: </span>
                    )}
                    {line.text}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
        {/*
         * Only the tail is announced, and only while streaming. Marking the
         * whole viewport live would read a million lines at a screen reader
         * user, which is worse than saying nothing.
         */}
        <p className="jg-sr-only" aria-live="polite">
          {streaming && last !== undefined ? `Latest line: ${last.text}` : ''}
        </p>
      </StateSurface>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capacity gauge
// ---------------------------------------------------------------------------

export interface CapacityGaugeProps extends StateProps {
  label: string;
  used: number;
  total: number;
  /** Held for queued runs but not yet consumed. Drawn as a dashed marker. */
  reserved?: number | undefined;
  unit?: string | undefined;
}

/**
 * How much of a pool is in use.
 *
 * `role="meter"`, not `progressbar`: a progress bar is a task advancing to
 * completion, and a GPU pool at 100% has not finished anything. The band --
 * ok, tight, exhausted -- is named in the note as well as coloured, and the
 * reserved marker is dashed as well as tinted.
 */
export function CapacityGauge({
  label,
  used,
  total,
  reserved,
  unit = '',
  state = 'ready',
  stateMessage,
  problem,
}: CapacityGaugeProps): JSX.Element {
  const safeTotal = total > 0 ? total : 1;
  const ratio = Math.min(1, Math.max(0, used / safeTotal));
  const band = ratio >= 1 ? 'exhausted' : ratio >= 0.85 ? 'tight' : 'ok';
  const suffix = unit === '' ? '' : ` ${unit}`;
  const valueText = `${used.toLocaleString()} of ${total.toLocaleString()}${suffix} in use, ${String(Math.round(ratio * 100))} percent`;

  return (
    <div className="jg-gauge" data-jg-band={band}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        minHeight="6rem"
      >
        <div className="jg-gauge__head">
          <span className="jg-gauge__label">{label}</span>
          <span className="jg-gauge__value">
            {used.toLocaleString()} / {total.toLocaleString()}
            {suffix}
          </span>
        </div>
        <div
          className="jg-gauge__track"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={used}
          aria-valuetext={valueText}
          aria-label={label}
        >
          <div className="jg-gauge__fill" style={{ width: `${String(ratio * 100)}%` }} />
          {reserved === undefined || reserved <= 0 ? null : (
            <div
              className="jg-gauge__reserved"
              aria-hidden="true"
              style={{
                left: `${String(ratio * 100)}%`,
                width: `${String(Math.min(100 - ratio * 100, (reserved / safeTotal) * 100))}%`,
              }}
            />
          )}
        </div>
        <p className="jg-gauge__note">
          {band === 'exhausted'
            ? 'Exhausted. New runs will queue until capacity frees.'
            : band === 'tight'
              ? 'Tight. Little headroom for an unplanned run.'
              : 'Healthy headroom.'}
          {reserved === undefined || reserved <= 0
            ? ''
            : ` ${reserved.toLocaleString()}${suffix} reserved for queued runs.`}
        </p>
      </StateSurface>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ledger entry viewer
// ---------------------------------------------------------------------------

export interface LedgerEntry {
  sequence: number;
  /** The event kind, e.g. `run.released`. */
  kind: string;
  recordedAt: string;
  actor: string;
  /** This entry's digest and the digest it chains to. */
  digest: string;
  previousDigest: string | null;
  /** The signed payload, already canonicalised for display. */
  payload: string;
  signature?: string | undefined;
  keyId?: string | undefined;
  /** Whether the signature and the chain link verified locally. */
  verified?: boolean | undefined;
}

export interface LedgerEntryViewerProps extends StateProps {
  entry: LedgerEntry;
}

/**
 * One append-only ledger entry, with its chain link and its seal.
 *
 * The verification result is rendered as a fact about this session's check,
 * not as an ambient property of the entry: "verified" with no statement of
 * what was verified against what is the kind of reassurance that survives a
 * broken chain. When the chain link or the signature fails, the seal says so
 * in words and the entry is still shown -- hiding a suspect entry is the last
 * thing an auditor wants.
 */
export function LedgerEntryViewer({
  entry,
  state = 'ready',
  stateMessage,
  problem,
}: LedgerEntryViewerProps): JSX.Element {
  const headingId = useId();
  const verified = entry.verified ?? false;

  return (
    <section className="jg-card" aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={`Ledger entry ${String(entry.sequence)}`}
        minHeight="14rem"
      >
        <div className="jg-card__head">
          <div>
            <h3 className="jg-card__title" id={headingId}>
              {entry.kind}
            </h3>
            <p className="jg-card__subtitle">entry #{entry.sequence}</p>
          </div>
          <span className="jg-ledger__seal" data-jg-verified={String(verified)}>
            <span aria-hidden="true">{verified ? '✓' : '✕'}</span>
            <span>
              {verified
                ? 'Signature and chain link verified'
                : 'Signature or chain link did not verify'}
            </span>
          </span>
        </div>

        <dl className="jg-facts">
          <dt>Recorded</dt>
          <dd>{entry.recordedAt}</dd>
          <dt>Actor</dt>
          <dd>{entry.actor}</dd>
          {entry.keyId === undefined ? null : (
            <>
              <dt>Signing key</dt>
              <dd data-jg-mono="true">{entry.keyId}</dd>
            </>
          )}
          {entry.signature === undefined ? null : (
            <>
              <dt>Signature</dt>
              <dd data-jg-mono="true">{entry.signature}</dd>
            </>
          )}
        </dl>

        <p className="jg-ledger__chain">
          <span className="jg-sr-only">Chains from </span>
          <span>{entry.previousDigest ?? 'genesis (no predecessor)'}</span>
          <span aria-hidden="true">→</span>
          <span className="jg-sr-only"> to this entry </span>
          <span>{entry.digest}</span>
        </p>

        {/* Focusable because it scrolls; see the note on the log viewport. */}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
        <pre className="jg-ledger__payload" tabIndex={0} aria-label="Signed payload">
          {entry.payload}
        </pre>
      </StateSurface>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Diff viewer
// ---------------------------------------------------------------------------

export type DiffOp = 'context' | 'add' | 'remove' | 'hunk';

export interface DiffLine {
  op: DiffOp;
  text: string;
  /** Line number on the left, absent for an addition. */
  oldNumber?: number | undefined;
  /** Line number on the right, absent for a removal. */
  newNumber?: number | undefined;
}

export interface DiffViewerProps extends StateProps {
  /** What is being compared, e.g. two policy bundle versions. */
  fromLabel: string;
  toLabel: string;
  lines: DiffLine[];
}

const DIFF_SIGN: Record<DiffOp, string> = {
  context: ' ',
  add: '+',
  remove: '-',
  hunk: '@',
};

const DIFF_WORD: Record<DiffOp, string> = {
  context: '',
  add: 'Added: ',
  remove: 'Removed: ',
  hunk: 'Section: ',
};

/**
 * A unified diff.
 *
 * The sign column is not decoration. A diff where added and removed lines are
 * distinguished only by a green and a red wash is unreadable to about one man
 * in twelve, so `+` and `-` are real characters in the markup and the
 * screen-reader text says "Added" and "Removed" in words.
 */
export function DiffViewer({
  fromLabel,
  toLabel,
  lines,
  state = 'ready',
  stateMessage,
  problem,
}: DiffViewerProps): JSX.Element {
  const shown = state === 'empty' ? [] : lines;
  const added = shown.filter((line) => line.op === 'add').length;
  const removed = shown.filter((line) => line.op === 'remove').length;

  return (
    <div className="jg-diff">
      <StateSurface
        state={shown.length === 0 && state === 'ready' ? 'empty' : state}
        stateMessage={stateMessage}
        problem={problem}
        label={`Difference between ${fromLabel} and ${toLabel}`}
        minHeight="12rem"
      >
        <div className="jg-diff__head">
          <span>
            {fromLabel} → {toLabel}
          </span>
          <span>
            {added} added, {removed} removed
          </span>
        </div>
        <div
          className="jg-diff__body"
          // Focusable because it scrolls: WCAG 2.1.1 requires a keyboard
          // user to be able to reach and scroll this region, and jsx-a11y
          // cannot see that it overflows.
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          role="group"
          aria-label={`Unified difference between ${fromLabel} and ${toLabel}: ${String(added)} lines added, ${String(removed)} lines removed`}
        >
          {shown.map((line, index) => (
            <div
              className="jg-diff__line"
              // Diff lines have no stable identity: the same text can appear
              // twice and a hunk header has no line number at all, so the
              // position in the hunk is the identity.
              key={`${line.op}:${String(index)}`}
              data-jg-op={line.op}
            >
              <span className="jg-diff__no" aria-hidden="true">
                {line.oldNumber ?? ''}
              </span>
              <span className="jg-diff__no" aria-hidden="true">
                {line.newNumber ?? ''}
              </span>
              <span className="jg-diff__sign" aria-hidden="true">
                {DIFF_SIGN[line.op]}
              </span>
              <span>
                {DIFF_WORD[line.op] === '' ? null : (
                  <span className="jg-sr-only">{DIFF_WORD[line.op]}</span>
                )}
                {line.text}
              </span>
            </div>
          ))}
        </div>
      </StateSurface>
    </div>
  );
}
