import type { JSX, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { StateSurface, type StateProps } from '../state/states';
import { type RunState } from '../tokens';
import { Badge, Button, Pill, Table, TextArea, type Tone } from '../primitives';
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

// ---------------------------------------------------------------------------
// Digest
// ---------------------------------------------------------------------------

/**
 * What a digest says instead of a hash, per state.
 *
 * A dash on its own is not an explanation. Each of these names the state in
 * words, so a screen reader user meeting a digest that is not there learns why
 * rather than reading a punctuation mark.
 */
const DIGEST_STATE: Record<string, string> = {
  loading: 'loading.',
  empty: 'none was recorded.',
  error: 'could not be loaded because the last request failed.',
  denied: 'not permitted: your role does not allow you to see it.',
  readOnly: 'read only.',
  partitioned: 'unavailable: the site is partitioned from the federation.',
};

/** How many characters of a hash are shown. AC-V8 fixes it at eight. */
export const DIGEST_SHOWN = 8;

export interface DigestProps extends StateProps {
  /** The full SHA-256, or whatever hash this is. Never pre-truncated. */
  value: string;
  /** What it is a digest of, for the accessible name. */
  of?: string | undefined;
  /** Copies to the clipboard. Omitted only where there is no clipboard. */
  onCopy?: ((value: string) => void) | undefined;
}

/**
 * A hash, truncated, with a route to the whole of it. AC-V8.
 *
 * "Hashes render truncated to eight characters with the full value available
 * and a copy control. No truncated hash without a route to the whole." Three
 * routes, because the three kinds of user need different ones: the title
 * attribute for a mouse, the visually hidden span for a screen reader, and the
 * copy button for anybody who has to paste it into a ticket.
 *
 * Truncation is presentational only. The full value is in the DOM and in the
 * clipboard, so nothing downstream ever receives eight characters and treats
 * them as a digest -- which is the failure mode that makes truncation
 * dangerous rather than merely lossy.
 */
export function Digest({
  value,
  of,
  onCopy,
  state = 'ready',
  stateMessage,
}: DigestProps): JSX.Element {
  const [copied, setCopied] = useState(false);
  const inert = state !== 'ready';
  const label = of === undefined ? 'digest' : `${of} digest`;
  const shown = value.slice(0, DIGEST_SHOWN);

  if (inert) {
    return (
      <span className="jg-digest" data-jg-state={state}>
        <span className="jg-digest__value" aria-hidden="true">
          —
        </span>
        <span>
          {label}: {stateMessage ?? DIGEST_STATE[state]}
        </span>
      </span>
    );
  }

  return (
    <span className="jg-digest">
      <span className="jg-digest__value" title={value}>
        {shown}
        <span aria-hidden="true">…</span>
      </span>
      {/* The whole of it, for a screen reader and for a text search of the page. */}
      <span className="jg-sr-only">
        {label}, full value {value}
      </span>
      <Button
        iconOnly
        icon={<span aria-hidden="true">⧉</span>}
        variant="ghost"
        size="sm"
        onClick={() => {
          onCopy?.(value);
          setCopied(true);
        }}
      >
        {`Copy the full ${label}`}
      </Button>
      {/* Polite, so it does not interrupt: a confirmation, not an alarm. */}
      <span className="jg-sr-only" aria-live="polite">
        {copied ? `${label} copied` : ''}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Run card
// ---------------------------------------------------------------------------

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
  /** How long it has been running, in seconds. */
  elapsedSeconds?: number | undefined;
  /** The appliance it is on. A node, never a site (Decision S12). */
  node?: string | undefined;
  /**
   * Recent step durations in seconds, most recent last.
   *
   * The estimate is computed from these rather than supplied, so that the rule
   * about when not to show one lives in one place. See `estimateRemaining`.
   */
  stepSeconds?: readonly number[] | undefined;
  /** Who submitted it. Custody matters (SAD 16A), so it is always on the face. */
  submittedBy?: string | undefined;
  actions?: RunAction[] | undefined;
}

/**
 * How many step times are needed before an estimate means anything.
 *
 * Fewer than this and the sample is one warm-up step and a bit of noise. Five
 * is the smallest number from which a spread can be read at all.
 */
export const ESTIMATE_MINIMUM_SAMPLES = 5;

/**
 * How much the step time may vary before it is not stable.
 *
 * The coefficient of variation: the standard deviation over the mean. A
 * threshold on the ratio rather than on the absolute spread, because a run
 * whose steps take 40 seconds and one whose steps take 4 are equally
 * predictable at the same ratio.
 */
export const ESTIMATE_MAXIMUM_VARIATION = 0.2;

export interface Estimate {
  /** Seconds remaining, or null when it would be a guess. */
  seconds: number | null;
  /** Why there is no estimate. Empty when there is one. */
  because: string;
}

/**
 * Seconds remaining, or a stated refusal to guess. Section 5.2.
 *
 * "Estimate is omitted rather than guessed when step time is not yet stable."
 * Omitted, and *said* -- a card that simply leaves the field out looks like a
 * card that forgot, and an operator planning around a run needs to know the
 * difference between "four hours" and "not knowable yet".
 *
 * Two ways it is not knowable: too few steps to judge, and steps whose times
 * disagree too much to extrapolate. The second is the one that matters in
 * practice, because a run that has just moved to a contended appliance has
 * plenty of samples and none of them predict the next one.
 */
export function estimateRemaining(
  stepSeconds: readonly number[],
  step: number,
  totalSteps: number,
): Estimate {
  const remaining = totalSteps - step;
  if (remaining <= 0) return { seconds: 0, because: '' };

  if (stepSeconds.length < ESTIMATE_MINIMUM_SAMPLES) {
    return {
      seconds: null,
      because: `not enough steps yet: ${String(stepSeconds.length)} of ${String(
        ESTIMATE_MINIMUM_SAMPLES,
      )} needed`,
    };
  }

  const mean = stepSeconds.reduce((total, value) => total + value, 0) / stepSeconds.length;
  if (mean <= 0) return { seconds: null, because: 'step time has not been measured' };

  const variance =
    stepSeconds.reduce((total, value) => total + (value - mean) ** 2, 0) / stepSeconds.length;
  const variation = Math.sqrt(variance) / mean;

  if (variation > ESTIMATE_MAXIMUM_VARIATION) {
    return {
      seconds: null,
      because: `step time is not stable: it varies by ${(variation * 100).toFixed(0)} per cent`,
    };
  }

  return { seconds: Math.round(mean * remaining), because: '' };
}

/** Seconds as an operator reads them. `4h 12m`, never `15120`. */
export function duration(seconds: number): string {
  if (seconds < 60) return `${String(Math.round(seconds))}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${String(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  return `${String(hours)}h ${String(minutes % 60)}m`;
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
  elapsedSeconds,
  node,
  stepSeconds = [],
  submittedBy,
  actions = [],
  state = 'ready',
  stateMessage,
  problem,
}: RunCardProps): JSX.Element {
  const headingId = useId();
  const hasProgress = step !== undefined && totalSteps !== undefined && totalSteps > 0;
  const pct = hasProgress ? Math.min(100, Math.round((step / totalSteps) * 100)) : 0;
  const estimate = hasProgress ? estimateRemaining(stepSeconds, step, totalSteps) : undefined;

  return (
    <section className="jg-card" aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={`Run ${runId}`}
        reserve="md"
      >
        <div className="jg-card__head">
          <div>
            <h3 className="jg-card__title" id={headingId}>
              {model}
            </h3>
            <p className="jg-card__subtitle">{runId}</p>
          </div>
          <Pill runState={runState} />
        </div>

        <dl className="jg-facts">
          {startedAt === undefined ? null : (
            <>
              <dt>Started</dt>
              <dd>{startedAt}</dd>
            </>
          )}
          {elapsedSeconds === undefined ? null : (
            <>
              <dt>Elapsed</dt>
              <dd>{duration(elapsedSeconds)}</dd>
            </>
          )}
          {node === undefined ? null : (
            <>
              <dt>Appliance</dt>
              <dd>{node}</dd>
            </>
          )}
          {/*
           * The estimate, or the reason there is not one. Section 5.2 says
           * "omitted rather than guessed"; omitting the row entirely would
           * read as a card that forgot, so the row stays and says why.
           */}
          {estimate === undefined ? null : (
            <>
              <dt>Remaining</dt>
              <dd data-jg-estimated={estimate.seconds === null ? 'false' : 'true'}>
                {estimate.seconds === null
                  ? `Not estimated — ${estimate.because}`
                  : `about ${duration(estimate.seconds)}`}
              </dd>
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

/**
 * One gate's measurement. Section 5.2: "value, baseline, margin and pass or
 * fail. Never a bare tick."
 *
 * All four are required, because the fourth without the first three is the
 * bare tick the sentence rules out. A gate that says "pass" and nothing else
 * asks an operator to trust a comparison they cannot see, and the margin is
 * the number that tells them whether the pass was comfortable or a hair's
 * breadth -- which is the only thing they can act on.
 */
export interface GateMeasurement {
  /** What was measured on the artefact. */
  value: number;
  /** What it was compared against. */
  baseline: number;
  /**
   * Value minus baseline, signed, as the ledger recorded it.
   *
   * Carried rather than computed here so the card shows the number the chain
   * shows. A margin recomputed in the browser is a second implementation of
   * the arithmetic a release was decided on.
   */
  margin: number;
  unit?: string | undefined;
}

export interface GateCardProps extends StateProps {
  gate: string;
  decision: GateDecision;
  /** The measurement behind the decision. Required: see `GateMeasurement`. */
  measurement: GateMeasurement;
  /** The policy bundle version the decision was taken under. */
  policyVersion?: string | undefined;
  /** Who waived it, when the decision is `waived`. Never blank on a waiver. */
  waivedBy?: string | undefined;
  waiverReason?: string | undefined;
  evidence: GateEvidence[];
}

/** A measured number, at the precision a gate margin is read at. */
function number(value: number, unit?: string): string {
  const text = value.toLocaleString('en-GB', { maximumFractionDigits: 4 });
  return unit === undefined ? text : `${text} ${unit}`;
}

/**
 * The same, with an explicit sign.
 *
 * A margin of 0.004 and one of -0.004 are opposite answers, and a minus sign
 * is easy to miss in a column of numbers. The plus is there so the two are the
 * same width and read as a pair.
 */
function signed(value: number, unit?: string): string {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '±';
  return `${sign}${number(Math.abs(value), unit)}`;
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
  measurement,
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
        reserve="lg"
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

        {/*
         * The measurement, always. This is the half of the card section 5.2 is
         * about: the verdict above is what was decided, and this is what it
         * was decided from. The margin carries its own sign so that "better"
         * and "worse" do not depend on knowing the direction of the gate.
         */}
        <dl className="jg-gate__measurement">
          <dt>Value</dt>
          <dd data-jg-numeric="true">{number(measurement.value, measurement.unit)}</dd>
          <dt>Baseline</dt>
          <dd data-jg-numeric="true">{number(measurement.baseline, measurement.unit)}</dd>
          <dt>Margin</dt>
          <dd
            data-jg-numeric="true"
            data-jg-sign={measurement.margin < 0 ? 'negative' : 'positive'}
          >
            {signed(measurement.margin, measurement.unit)}
          </dd>
        </dl>

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
                  <Digest value={item.digest} of={item.kind} />
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
// Evidence panel
// ---------------------------------------------------------------------------

export interface EvidenceRow {
  gate: string;
  /** What the gate asks, in words. Shown so the number has a meaning. */
  statement: string;
  measurement: GateMeasurement;
  passed: boolean;
}

export interface EvidencePanelProps extends StateProps {
  /** The suite these came from, `name/version`. */
  suiteVersion: string;
  rows: EvidenceRow[];
  /** What the artefact was measured on. Rendered under AC-V8. */
  artefactDigest?: string | undefined;
}

/**
 * The full gate set, above any decision control. Section 5.2, and AC-S8.
 *
 * "Sits above any decision control. Sortable by margin so the tightest result
 * is findable." The second sentence is the interesting one: an approver
 * scanning six gates does not want them alphabetically, they want to know
 * which one nearly failed. So margin is the default sort, ascending, and the
 * tightest result is the first row without anybody having to look for it.
 *
 * Signed margins sort by their sign, not their size: a gate that failed by
 * 0.002 sorts before one that passed by 0.001, which is the order an approver
 * reads them in.
 */
export function EvidencePanel({
  suiteVersion,
  rows,
  artefactDigest,
  state = 'ready',
  stateMessage,
  problem,
}: EvidencePanelProps): JSX.Element {
  const headingId = useId();
  const shown = state === 'empty' ? [] : rows;
  const tightest = [...shown].sort((a, b) => a.measurement.margin - b.measurement.margin)[0];

  return (
    <section className="jg-card jg-evidence" aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label="Gate evidence"
        reserve="lg"
      >
        <div className="jg-card__head">
          <div>
            <h3 className="jg-card__title" id={headingId}>
              Gate evidence
            </h3>
            <p className="jg-card__subtitle">suite {suiteVersion}</p>
          </div>
          {artefactDigest === undefined ? null : <Digest value={artefactDigest} of="artefact" />}
        </div>

        {/*
         * The tightest result in a sentence, above the table. The sort makes it
         * findable; this makes it unmissable, which is what an approver about
         * to sign actually needs.
         */}
        {tightest === undefined ? null : (
          <p className="jg-evidence__tightest">
            Tightest result: {tightest.gate} {tightest.passed ? 'passed' : 'failed'} by{' '}
            {signed(tightest.measurement.margin, tightest.measurement.unit)}.
          </p>
        )}

        <Table
          caption={`Gate results for suite ${suiteVersion}`}
          rows={shown}
          rowKey={(row) => row.gate}
          sort={{ key: 'margin', direction: 'ascending' }}
          columns={[
            {
              key: 'gate',
              header: 'Gate',
              render: (row) => row.gate,
              sortKey: (row) => row.gate,
            },
            { key: 'statement', header: 'Requirement', render: (row) => row.statement },
            {
              key: 'value',
              header: 'Value',
              numeric: true,
              render: (row) => number(row.measurement.value, row.measurement.unit),
              sortKey: (row) => row.measurement.value,
            },
            {
              key: 'baseline',
              header: 'Baseline',
              numeric: true,
              render: (row) => number(row.measurement.baseline, row.measurement.unit),
              sortKey: (row) => row.measurement.baseline,
            },
            {
              key: 'margin',
              header: 'Margin',
              numeric: true,
              render: (row) => signed(row.measurement.margin, row.measurement.unit),
              sortKey: (row) => row.measurement.margin,
            },
            {
              key: 'result',
              header: 'Result',
              // Never a bare tick: the word, and the glyph beside it as
              // decoration for anybody who reads the shape faster.
              render: (row) => (
                <span data-jg-passed={String(row.passed)}>
                  <span aria-hidden="true">{row.passed ? '✓ ' : '✕ '}</span>
                  {row.passed ? 'Passed' : 'Failed'}
                </span>
              ),
              sortKey: (row) => (row.passed ? 1 : 0),
            },
          ]}
        />
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
  /**
   * The identifiers this artefact claims to derive from.
   *
   * This is what makes a gap impossible to omit. A node states what it came
   * from; the tree checks that each of those is present; and anything claimed
   * and absent is rendered as a gap node saying so. A caller cannot produce a
   * shorter tree by leaving an ancestor out, because leaving it out is exactly
   * what the check looks for.
   *
   * A node with no claim is a root of the known lineage, not a node whose
   * ancestry is fine.
   */
  derivedFrom?: readonly string[] | undefined;
}

export interface LineageTreeProps extends StateProps {
  label: string;
  roots: LineageNode[];
  selectedId?: string | undefined;
  onSelect?: ((id: string) => void) | undefined;
}

/** The `kind` a gap node carries. Reserved: a real artefact may not use it. */
export const GAP_KIND = 'gap';

/**
 * Insert a marked node wherever a claimed ancestor is missing. AC-S11.
 *
 * "Lineage renders a gap as a marked node stating what is missing, never as a
 * shorter tree." The rule is enforced here rather than asked of the caller,
 * because a caller that knew to mark its gaps would not have produced one.
 *
 * Every `derivedFrom` identifier is looked up across the whole tree, not only
 * among a node's own children: an artefact may derive from something that
 * appears elsewhere in the lineage, and that is present rather than missing.
 * What is left -- claimed by somebody, present nowhere -- becomes a child of
 * the node that claimed it, labelled with the identifier it could not find.
 *
 * The result is that the only way to render a lineage with no gap node is to
 * supply one with no missing ancestor.
 */
export function withGaps(roots: LineageNode[]): LineageNode[] {
  const present = new Set(collectIds(roots));

  function walk(nodes: LineageNode[]): LineageNode[] {
    return nodes.map((node) => {
      const missing = (node.derivedFrom ?? []).filter((id) => !present.has(id));
      const children = walk(node.children ?? []);
      const gaps: LineageNode[] = missing.map((id) => ({
        id: `${node.id}::gap::${id}`,
        kind: GAP_KIND,
        label: `Missing: ${id}`,
        derivedFrom: [],
      }));
      // Gaps first, so the thing that is wrong is the first child read rather
      // than the last one scrolled to.
      return { ...node, children: [...gaps, ...children] };
    });
  }

  return walk(roots);
}

/** How many gap nodes a lineage holds, at any depth. */
export function countGaps(nodes: LineageNode[]): number {
  return nodes.reduce(
    (total, node) =>
      total + (node.kind === GAP_KIND ? 1 : 0) + countGaps([...(node.children ?? [])]),
    0,
  );
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
  // Derived, not taken. `roots` is what the caller supplied; `complete` is
  // that with every claimed-and-absent ancestor marked, and it is the only
  // thing rendered below.
  const complete = useMemo(() => withGaps(roots), [roots]);
  const gaps = useMemo(() => countGaps(complete), [complete]);
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
              {/*
               * The treeitem carries the tabindex and the click, rather than a
               * button inside it.
               *
               * It was a button, and that made every digest's copy control a
               * button inside a button -- which axe reports as
               * `nested-interactive` and which a screen reader reads as one
               * control with two names. The ARIA tree pattern does not want a
               * button here anyway: a treeitem *is* the widget, and the roving
               * tabindex is what makes it operable.
               */}
              {/*
               * The keyboard listener is on the tree, not on each row: the
               * whole widget is one key handler and a roving tabindex, which
               * is the ARIA tree pattern. `jsx-a11y` looks for a listener on
               * the element it finds the click on and cannot see the one a
               * level up.
               */}
              {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */}
              <span
                className="jg-tree__row jg-tree__node"
                data-jg-node={node.id}
                data-jg-kind={node.kind}
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
              </span>
              {/*
               * Outside the row, so the copy control is a sibling of the
               * treeitem's own hit area rather than a control inside a control.
               */}
              {node.digest === undefined ? null : (
                <span className="jg-tree__digest">
                  <Digest value={node.digest} of={node.kind} />
                </span>
              )}
              {children.length > 0 && open ? renderNodes(children, level + 1) : null}
            </li>
          );
        })}
      </ul>
    );
  }

  const shown = state === 'empty' ? [] : complete;

  return (
    <div className="jg-tree">
      <StateSurface
        state={shown.length === 0 && state === 'ready' ? 'empty' : state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        reserve="md"
      >
        {/*
         * Stated above the tree as well as marked within it. A gap six levels
         * down in a collapsed branch is a gap somebody has to go looking for,
         * and the whole point of AC-S11 is that they should not have to.
         */}
        {gaps > 0 ? (
          <p className="jg-tree__gaps" role="status">
            {gaps === 1
              ? 'One ancestor of this lineage is missing and is marked below.'
              : `${String(gaps)} ancestors of this lineage are missing and are marked below.`}
          </p>
        ) : null}
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
  /** The merge point that was chosen. Highlighted, and explained beneath. */
  selectedId?: string | undefined;
}

/**
 * What choosing this arm cost and what it bought, in a sentence.
 *
 * Generated, never written. A hard-coded sentence is a sentence that stops
 * being true the first time the numbers move, and this one has to be read by
 * somebody deciding whether to accept a merge. Each metric is compared against
 * the best arm on that metric: a shortfall is what the choice gave up, and
 * being the best is what it bought.
 *
 * Returns an empty string when nothing was selected, rather than a sentence
 * about nothing.
 */
export function tradeSentence(
  metrics: SweepMetric[],
  arms: SweepArm[],
  selectedId: string | undefined,
): string {
  const selected = arms.find((arm) => arm.id === selectedId);
  if (selected === undefined) return '';

  const gave: string[] = [];
  const won: string[] = [];

  for (const metric of metrics) {
    const value = selected.values[metric.key];
    if (value === undefined) continue;

    const others = arms
      .filter((arm) => arm.id !== selected.id)
      .map((arm) => arm.values[metric.key])
      .filter((candidate): candidate is number => candidate !== undefined);
    if (others.length === 0) continue;

    const best = metric.higherIsBetter ? Math.max(...others) : Math.min(...others);
    const behind = metric.higherIsBetter ? best - value : value - best;

    if (behind > 0) {
      gave.push(`${format(behind)} on ${metric.label}`);
    } else if (behind < 0) {
      won.push(`${format(-behind)} on ${metric.label}`);
    }
  }

  if (gave.length === 0 && won.length === 0) {
    return `${selected.label} matches every other point on every metric measured.`;
  }
  if (gave.length === 0) {
    return `${selected.label} is the best point measured, leading by ${list(won)}.`;
  }
  if (won.length === 0) {
    return `${selected.label} gives up ${list(gave)} against the best point, and leads on nothing.`;
  }
  return `${selected.label} gives up ${list(gave)} to gain ${list(won)}.`;
}

function format(value: number): string {
  return value.toLocaleString('en-GB', { maximumFractionDigits: 4 });
}

/** `a`, `a and b`, `a, b and c`. British, and readable aloud. */
function list(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] ?? ''}`;
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
  selectedId,
  state = 'ready',
  stateMessage,
  problem,
}: SweepMatrixProps): JSX.Element {
  const shown = useMemo(() => (state === 'empty' ? [] : arms), [state, arms]);

  const trade = useMemo(
    () => tradeSentence(metrics, arms, selectedId),
    [metrics, arms, selectedId],
  );
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
      reserve="lg"
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
              <tr key={arm.id} data-jg-selected={arm.id === selectedId ? 'true' : undefined}>
                <th scope="row">
                  {arm.id === selectedId ? (
                    <>
                      <span aria-hidden="true">◆ </span>
                      <span className="jg-sr-only">Selected: </span>
                    </>
                  ) : null}
                  {arm.label}
                </th>
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
                        <>
                          <span aria-hidden="true">—</span>
                          <span className="jg-sr-only">Not measured</span>
                        </>
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
      {/*
       * AC-S12: the selected point is marked and the trade is stated in words
       * beneath the matrix. A matrix on its own asks the reader to do the
       * comparison; this is the comparison done.
       */}
      {trade === '' ? null : <p className="jg-sweep__trade">{trade}</p>}
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
  /**
   * AC-X8: on by default, and released by scrolling up.
   *
   * Released rather than toggled off, because the gesture that means "stop
   * moving, I am reading this" is scrolling away from the bottom, and asking
   * somebody to find a button first is asking them to lose the line they were
   * looking at.
   */
  const [following, setFollowing] = useState(true);
  const viewport = useRef<HTMLDivElement | null>(null);
  const shown = state === 'empty' ? [] : lines;

  const first = Math.max(0, Math.floor(scrollTop / LOG_ROW_HEIGHT) - LOG_OVERSCAN);
  const count = Math.ceil(viewportHeight / LOG_ROW_HEIGHT) + LOG_OVERSCAN * 2;
  const window = shown.slice(first, first + count);
  const last = shown[shown.length - 1];

  // While following, a new line moves the viewport with it. The effect runs on
  // the line count rather than on every render, so a scroll that is already at
  // the bottom is not fought over.
  useEffect(() => {
    if (!following) return;
    const node = viewport.current;
    if (node !== null) node.scrollTop = node.scrollHeight;
  }, [shown.length, following]);

  return (
    <div className="jg-log">
      <StateSurface
        state={shown.length === 0 && state === 'ready' ? 'empty' : state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        reserve="xl"
      >
        <div className="jg-log__bar">
          <span>{label}</span>
          <span>
            {shown.length.toLocaleString()} lines
            {streaming ? ', still writing' : ''}
          </span>
          {/*
           * The state, in words rather than in the position of a scrollbar.
           * AC-X8 asks for it to be stated; a button that says which way it
           * currently is states it and offers the way back in one control.
           */}
          <span className="jg-log__follow">
            <span data-jg-following={following ? 'true' : 'false'}>
              {following ? 'Following the tail' : 'Not following: scrolled back'}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFollowing(true);
                const node = viewport.current;
                if (node !== null) node.scrollTop = node.scrollHeight;
              }}
              state={following ? 'readOnly' : 'ready'}
              stateMessage="Already following the tail."
            >
              Follow the tail
            </Button>
          </span>
        </div>
        <div
          className="jg-log__viewport"
          ref={viewport}
          // Focusable because it scrolls: WCAG 2.1.1 requires a keyboard
          // user to be able to reach and scroll this region, and jsx-a11y
          // cannot see that it overflows.
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          role="log"
          aria-label={`${label}, ${shown.length.toLocaleString()} lines, showing ${String(first + 1)} to ${String(Math.min(shown.length, first + window.length))}, ${following ? 'following the tail' : 'not following the tail'}`}
          onScroll={(event) => {
            const node = event.currentTarget;
            setScrollTop(node.scrollTop);
            setViewportHeight(node.clientHeight);
            // Within a row of the bottom counts as the bottom: a scroll that
            // lands a pixel short should not read as "the user scrolled away".
            const atBottom =
              node.scrollHeight - node.scrollTop - node.clientHeight <= LOG_ROW_HEIGHT;
            setFollowing(atBottom);
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
         * Only the tail is announced, only while streaming, and only while
         * following. Marking the whole viewport live would read a million
         * lines at a screen reader user; announcing the tail to somebody who
         * has scrolled back to read something is the same interruption in
         * miniature.
         */}
        <p className="jg-sr-only" aria-live="polite">
          {streaming && following && last !== undefined ? `Latest line: ${last.text}` : ''}
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
        reserve="sm"
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
        reserve="lg"
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
        reserve="lg"
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

// ---------------------------------------------------------------------------
// Spec editor
// ---------------------------------------------------------------------------

/** One thing wrong with the specification, at the place it is wrong. */
export interface SpecProblem {
  /** A JSON pointer, `/train/params/rank`, or empty for the document. */
  pointer: string;
  message: string;
}

/**
 * What validates a specification.
 *
 * A function rather than a schema object, so the caller brings the real
 * validator -- the same compiled JSON Schema the API validates against -- and
 * the component does not carry a second implementation of the rules. A second
 * validator is a second opinion, and the one that matters is the server's.
 */
export type SpecValidator = (parsed: unknown) => SpecProblem[];

export interface SpecEditorProps extends StateProps {
  label: string;
  value: string;
  onChange?: ((value: string) => void) | undefined;
  /** Validates the parsed document. See `SpecValidator`. */
  validate?: SpecValidator | undefined;
  /** The primary action. Section 5.2: "dry run before submit". */
  onDryRun?: ((value: string) => void) | undefined;
  /** The secondary action, and only reachable once a dry run has been done. */
  onSubmit?: ((value: string) => void) | undefined;
  /** What the last dry run said. Rendered above the actions. */
  dryRunResult?: string | undefined;
}

/**
 * Compose a run specification. Section 5.2.
 *
 * Three rules from the specification's own table, and each is a thing that
 * goes wrong when it is left out.
 *
 * **Validation is inline.** The schema is checked as the operator types, and
 * every problem names the pointer it is at. A specification rejected by the
 * API twenty seconds after submission is a specification whose author has
 * already moved on.
 *
 * **It never submits on Enter.** A specification is a multi-line document and
 * Enter is how you get a new line in one. A form that submits on Enter turns
 * an ordinary keystroke into an irreversible act -- and this act spends days
 * of GPU time.
 *
 * **Dry run is primary and submit is secondary.** Not a visual preference: the
 * dry run is what turns a syntactically valid document into one somebody has
 * seen the consequences of, and submit stays inert until it has been done.
 */
export function SpecEditor({
  label,
  value,
  onChange,
  validate,
  onDryRun,
  onSubmit,
  dryRunResult,
  state = 'ready',
  stateMessage,
  problem,
}: SpecEditorProps): JSX.Element {
  const headingId = useId();
  const [dryRunFor, setDryRunFor] = useState<string | null>(null);

  const problems = useMemo((): SpecProblem[] => {
    if (value.trim() === '') return [{ pointer: '', message: 'The specification is empty.' }];
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch (error) {
      // The parser's own message names the offset, which is more use than
      // "invalid JSON" and is what an editor would show.
      return [{ pointer: '', message: `Not valid JSON: ${(error as Error).message}` }];
    }
    return validate?.(parsed) ?? [];
  }, [value, validate]);

  const valid = problems.length === 0;
  // A dry run is spent the moment the document changes: what was checked is
  // not what would be submitted.
  const dryRunIsCurrent = dryRunFor === value;

  return (
    <section className="jg-card jg-spec" aria-labelledby={headingId}>
      <StateSurface
        state={state}
        stateMessage={stateMessage}
        problem={problem}
        label={label}
        reserve="xl"
      >
        <h3 className="jg-card__title" id={headingId}>
          {label}
        </h3>

        {/*
         * A form, so the browser's own semantics apply -- and `onSubmit`
         * prevented, so they do not fire. The two together are what makes
         * "never submits on Enter" true rather than asserted: a form with no
         * submit handler still submits, and a div with buttons in it is not a
         * form to a screen reader.
         */}
        <form
          className="jg-spec__form"
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <TextArea
            label="Specification"
            hint="JSON. Validated against the run specification schema as you type."
            value={value}
            rows={16}
            state={state}
            onChange={(next) => {
              onChange?.(next);
            }}
          />

          <div className="jg-spec__problems" role="status" aria-live="polite">
            {valid ? (
              <p data-jg-valid="true">
                <span aria-hidden="true">✓ </span>
                The specification validates against the schema.
              </p>
            ) : (
              <>
                <p data-jg-valid="false">
                  <span aria-hidden="true">✕ </span>
                  {problems.length === 1
                    ? 'One problem with the specification:'
                    : `${String(problems.length)} problems with the specification:`}
                </p>
                <ul>
                  {problems.map((item) => (
                    <li key={`${item.pointer}:${item.message}`}>
                      {item.pointer === '' ? null : (
                        <code className="jg-spec__pointer">{item.pointer}</code>
                      )}{' '}
                      {item.message}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {dryRunResult === undefined || !dryRunIsCurrent ? null : (
            <p className="jg-spec__dry-run">{dryRunResult}</p>
          )}

          <div className="jg-card__actions">
            <Button
              variant="primary"
              type="button"
              state={state === 'ready' && valid ? 'ready' : 'readOnly'}
              stateMessage={
                valid ? undefined : 'The specification does not validate, so it cannot be run.'
              }
              onClick={() => {
                setDryRunFor(value);
                onDryRun?.(value);
              }}
            >
              Dry run
            </Button>
            <Button
              variant="secondary"
              type="button"
              state={state === 'ready' && valid && dryRunIsCurrent ? 'ready' : 'readOnly'}
              stateMessage={
                valid
                  ? 'Dry run this specification first. Submit is available once it has been checked.'
                  : 'The specification does not validate, so it cannot be submitted.'
              }
              onClick={() => {
                onSubmit?.(value);
              }}
            >
              Submit
            </Button>
          </div>
        </form>
      </StateSurface>
    </section>
  );
}
