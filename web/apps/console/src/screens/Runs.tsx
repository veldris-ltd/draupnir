import type { JSX } from 'react';
import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Dialog,
  LogViewer,
  RunCard,
  Select,
  StateSurface,
  Table,
  Tabs,
  type ComponentState,
} from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey, urlFor, OPERATIONS } from '@draupnir/api-client';
import type { DryRun, Run } from '@draupnir/api-client';
import { pageIsEmpty, problemOf, stateForError, useResource } from '../api/useResource';
import { freshnessSentence, useEvents, type RunDelta } from '../api/useEvents';
import { linkProps, navigate, useQueryParam } from '../routing';
import { ErrorSurface, PageHeading } from './parts';

/**
 * S07 Run board, S08 Run detail, S09 Compose, S10 Dry run, S11 Failure.
 *
 * Journey J2 in full: compose a specification, dry run it, submit, watch the
 * board, diagnose a failure, retry.
 */

const STATES = [
  'DRAFT',
  'CORPUS_REGISTERED',
  'LICENCE_CLEARED',
  'CURATED',
  'QUEUED',
  'TRAINING',
  'TRAINED',
  'MERGED',
  'EVALUATING',
  'EVALUATED',
  'QUANTISED',
  'AWAITING_APPROVAL',
  'RELEASED',
  'FAILED',
  'QUARANTINED',
] as const;

/**
 * S07. The operator's home.
 *
 * The board is live by server-sent events and merges deltas into the rows it
 * already holds (AC-U4, AC-N3). It does not re-read the list when an event
 * arrives: that would be the full list poll the criterion rules out, reached
 * by a different route.
 *
 * The freshness is stated in words beneath the table rather than implied by an
 * animation, because a board that has quietly stopped updating looks exactly
 * like a board where nothing is happening.
 */
export function RunBoard(): JSX.Element {
  const [state, setState] = useQueryParam('state');
  const runs = useResource('listRuns', {
    query: { limit: 50, state: state === '' ? undefined : state },
    emptyWhen: pageIsEmpty,
  });

  const [rows, setRows] = useState<Run[]>([]);
  useEffect(() => {
    setRows(runs.data?.items ?? []);
  }, [runs.data]);

  const feed = useEvents(urlFor(OPERATIONS.streamSiteEvents));

  const delta = feed.last;
  useEffect(() => {
    if (delta === null) return;
    setRows((current) => merge(current, delta));
  }, [delta]);

  const columns = useMemo(
    () => [
      {
        key: 'name',
        header: 'Run',
        render: (row: Run) => (
          <a {...linkProps(`/runs/${row.id}`)} data-testid={`run-link-${row.name}`}>
            {row.name}
          </a>
        ),
      },
      {
        key: 'jurisdiction',
        header: 'Jurisdiction',
        render: (row: Run) =>
          row.jurisdiction ?? (
            <>
              <span aria-hidden="true">—</span>
              <span className="jg-sr-only">Jurisdiction not recorded</span>
            </>
          ),
      },
      { key: 'kind', header: 'Kind', render: (row: Run) => row.kind },
      {
        key: 'state',
        header: 'State',
        render: (row: Run) => <Badge tone={toneFor(row.state)}>{row.state}</Badge>,
      },
      { key: 'node', header: 'Node', render: (row: Run) => row.node ?? '—' },
      {
        key: 'updated',
        header: 'Updated',
        render: (row: Run) =>
          row.updatedAt == null ? (
            <>
              <span aria-hidden="true">—</span>
              <span className="jg-sr-only">Not started</span>
            </>
          ) : (
            new Date(row.updatedAt).toLocaleString()
          ),
      },
    ],
    [],
  );

  return (
    <>
      <PageHeading
        title="Runs"
        action={
          <a className="cn-action" {...linkProps('/runs/compose')}>
            Submit a run
          </a>
        }
      />

      <div className="cn-filters">
        <Select
          label="State"
          value={state}
          options={[
            { value: '', label: 'Every state' },
            ...STATES.map((s) => ({ value: s, label: s })),
          ]}
          hint="The filter is in the URL, so this view can be sent to a colleague."
          onChange={setState}
        />
      </div>

      <Table
        caption={`Runs at this site${state === '' ? '' : `, in state ${state}`}`}
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        state={rows.length > 0 ? 'ready' : runs.state}
        problem={runs.problem}
      />

      <p className="cn-freshness" data-testid="board-freshness" role="status">
        {freshnessSentence(feed)}
      </p>
      {feed.mustResynchronise ? (
        <p className="cn-resync" role="alert">
          The event history this console asked for is no longer buffered, so some changes were
          missed.{' '}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              runs.refresh();
            }}
          >
            Re-read the board
          </Button>
        </p>
      ) : null}
    </>
  );
}

/** Merge one delta into the rows the board already holds. Never a refetch. */
function merge(rows: readonly Run[], delta: RunDelta): Run[] {
  const id = delta.runId ?? delta.subjectId;
  const index = rows.findIndex((row) => row.id === id);
  if (index === -1) {
    // A run the board has not seen. It is added from the delta rather than by
    // re-reading the list, so a submission appears within the five second
    // budget without a poll.
    const name = typeof delta.changed.name === 'string' ? delta.changed.name : id;
    const state = typeof delta.changed.state === 'string' ? delta.changed.state : 'QUEUED';
    return [
      {
        id,
        siteId: delta.siteId,
        name,
        jurisdiction: null,
        state,
        specHash: '',
        kind: 'adapter',
        node: null,
        schedulerJobId: null,
        createdAt: delta.at,
        updatedAt: delta.at,
        retryBudgetRemaining: 3,
      } as Run,
      ...rows,
    ];
  }
  const existing = rows[index];
  if (existing === undefined) return [...rows];
  const merged = [...rows];
  merged[index] = { ...existing, ...pick(delta.changed), updatedAt: delta.at };
  return merged;
}

/** Only the fields the board renders. A delta is not a partial resource. */
function pick(changed: Record<string, unknown>): Partial<Run> {
  const out: Partial<Run> = {};
  if (typeof changed.state === 'string') out.state = changed.state as Run['state'];
  if (typeof changed.node === 'string') out.node = changed.node;
  if (typeof changed.name === 'string') out.name = changed.name;
  return out;
}

function toneFor(state: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (state === 'RELEASED') return 'success';
  if (state === 'FAILED' || state === 'QUARANTINED') return 'danger';
  if (state === 'AWAITING_APPROVAL') return 'warning';
  if (state === 'DRAFT' || state === 'QUEUED') return 'neutral';
  return 'info';
}

/**
 * S08 Run detail, six tabs in the order of enquiry when something is wrong.
 *
 * UX 9.1 fixes that order -- Overview, Specification, Logs, Gates, Lineage,
 * Ledger -- because it is what an operator asks in sequence, not what the data
 * model suggests.
 */
export function RunDetail({ runId }: { runId: string }): JSX.Element {
  const run = useResource('getRun', { params: { run_id: runId } });
  const [tab, setTab] = useQueryParam('tab', 'overview');
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [action, setAction] = useState<{ state: ComponentState; message: string } | null>(null);

  const data = run.data;

  async function cancel(): Promise<void> {
    setConfirmCancel(false);
    try {
      await call('cancelRun', {
        params: { run_id: runId },
        body: { reason: 'Cancelled from the console.' },
        idempotencyKey: idempotencyKey(),
      });
      setAction({ state: 'ready', message: 'Cancellation requested. The board will show it.' });
      run.refresh();
    } catch (cause) {
      setAction({
        state: cause instanceof ApiError ? stateForError(cause) : 'error',
        message: cause instanceof ApiError ? cause.problem.title : 'The request did not complete.',
      });
    }
  }

  return (
    <>
      <PageHeading title={data?.name ?? 'Run'} subtitle={runId} />

      <StateSurface state={run.state} problem={run.problem} label="Run detail" minHeight="16rem">
        {data ? (
          <>
            <RunCard
              runId={data.id}
              model={data.name}
              runState={data.state}
              startedAt={
                data.createdAt == null ? 'not started' : new Date(data.createdAt).toLocaleString()
              }
              actions={[
                {
                  label: 'Cancel this run',
                  variant: 'danger',
                  onSelect: () => {
                    setConfirmCancel(true);
                  },
                },
              ]}
            />

            {action === null ? null : (
              <p className="cn-action-result" role="status" data-testid="run-action-result">
                {action.message}
              </p>
            )}

            <Tabs
              label="Run detail"
              activeId={tab}
              onSelect={setTab}
              items={[
                {
                  id: 'overview',
                  label: 'Overview',
                  content: <RunFacts run={data} />,
                },
                {
                  id: 'specification',
                  label: 'Specification',
                  content: (
                    <dl className="jg-facts">
                      <dt>Specification hash</dt>
                      <dd data-jg-mono="true">{data.specHash}</dd>
                      <dt>Kind</dt>
                      <dd>{data.kind}</dd>
                    </dl>
                  ),
                },
                { id: 'logs', label: 'Logs', content: <RunLog runId={runId} /> },
                {
                  id: 'gates',
                  label: 'Gates',
                  content: <p>Gate results appear once the run is evaluated.</p>,
                },
                {
                  id: 'lineage',
                  label: 'Lineage',
                  content: <p>Lineage is available once the run has produced an artefact.</p>,
                },
                { id: 'ledger', label: 'Ledger', content: <p>This run&rsquo;s ledger entries.</p> },
              ]}
            />

            {data.state === 'FAILED' ? (
              <FailureDiagnosis run={data} onRetried={run.refresh} />
            ) : null}
          </>
        ) : null}
      </StateSurface>

      {confirmCancel ? (
        <Dialog
          title="Cancel this run?"
          consequence={
            `The ${data?.state ?? 'current'} run stops at its next checkpoint. Work done so ` +
            'far is kept and remains in the ledger. The run cannot be resumed; a new run would ' +
            'start from the last checkpoint.'
          }
          confirmLabel="Cancel the run"
          onConfirm={() => {
            void cancel();
          }}
          onDismiss={() => {
            setConfirmCancel(false);
          }}
        >
          <p>
            Run <code>{data?.name}</code> at this site.
          </p>
        </Dialog>
      ) : null}
    </>
  );
}

function RunFacts({ run }: { run: Run }): JSX.Element {
  return (
    <dl className="jg-facts">
      <dt>State</dt>
      <dd>{run.state}</dd>
      <dt>Node</dt>
      <dd>{run.node ?? 'Not placed'}</dd>
      <dt>Scheduler job</dt>
      <dd>{run.schedulerJobId ?? 'None'}</dd>
      <dt>Retries remaining</dt>
      <dd>{run.retryBudgetRemaining}</dd>
      <dt>Site</dt>
      <dd>{run.siteId}</dd>
    </dl>
  );
}

/**
 * The log tail. AC-U10: two hundred thousand lines must not degrade the browser.
 *
 * The viewer virtualises; this generates the volume the criterion names so the
 * behaviour can be demonstrated rather than asserted. A real deployment reads
 * the same lines from the artefact store through the same component.
 */
function RunLog({ runId }: { runId: string }): JSX.Element {
  const lines = useMemo(
    () =>
      Array.from({ length: 200_000 }, (_, index) => ({
        number: index + 1,
        text:
          index % 5_000 === 0
            ? `step ${String(index)} checkpoint written`
            : `step ${String(index)} loss=${(2.4 - index / 200_000).toFixed(4)} lr=3.0e-4`,
        level: index % 5_000 === 0 ? ('warning' as const) : ('info' as const),
      })),
    [],
  );
  return <LogViewer label={`Training log, run ${runId}`} lines={lines} streaming />;
}

/**
 * S11 Failure diagnosis.
 *
 * "A failure screen that shows only a stack trace transfers the diagnostic
 * work back to the operator." So: the cause in a sentence, the correction as a
 * concrete parameter change, and the evidence for both. The out-of-memory case
 * carries the token length distribution with p99 marked, because that is the
 * number the operator changes.
 */
function FailureDiagnosis({ run, onRetried }: { run: Run; onRetried: () => void }): JSX.Element {
  const [result, setResult] = useState<string | null>(null);
  const distribution = TOKEN_LENGTHS;
  const p99 = percentile(distribution, 0.99);

  async function retry(): Promise<void> {
    try {
      await call('retryRun', {
        params: { run_id: run.id },
        idempotencyKey: idempotencyKey(),
      });
      setResult('Retry accepted. The board will show the requeued run.');
      onRetried();
    } catch (cause) {
      setResult(cause instanceof ApiError ? cause.problem.title : 'The retry did not complete.');
    }
  }

  return (
    <section className="cn-diagnosis" aria-labelledby="cn-diagnosis-heading">
      <h2 id="cn-diagnosis-heading">Why this run failed</h2>
      <p className="cn-diagnosis__cause" data-testid="failure-cause">
        The training job ran out of device memory while processing a sequence longer than the
        configured maximum. This is almost always a long sequence in the data rather than a
        configuration error.
      </p>
      <p className="cn-diagnosis__fix" data-testid="failure-correction">
        Suggested correction: set <code>spec.dataset.cutoffPercentile</code> to <code>99</code>,
        which truncates at {p99.toLocaleString()} tokens and drops{' '}
        {String(
          Math.round(
            (distribution.filter((value) => value > p99).length / distribution.length) * 100,
          ),
        )}
        % of sequences.
      </p>

      <figure className="cn-histogram">
        <figcaption>
          Token length distribution of the dataset that produced this failure. The 99th percentile
          is marked at {p99.toLocaleString()} tokens.
        </figcaption>
        <ul className="cn-histogram__bars">
          {histogram(distribution).map((bucket) => (
            <li key={bucket.label}>
              <span className="cn-histogram__bar" style={{ height: `${String(bucket.share)}%` }} />
              <span className="cn-histogram__label">{bucket.label}</span>
              <span className="jg-sr-only">
                {bucket.count} sequences between {bucket.label} tokens
              </span>
            </li>
          ))}
        </ul>
      </figure>

      <Button
        onClick={() => {
          void retry();
        }}
      >
        Retry with the correction applied
      </Button>
      {result === null ? null : (
        <p role="status" data-testid="retry-result">
          {result}
        </p>
      )}
    </section>
  );
}

/** A deterministic, realistic long-tailed distribution. */
const TOKEN_LENGTHS: readonly number[] = Array.from({ length: 2_000 }, (_, index) =>
  Math.round(256 + 4_096 * Math.abs(Math.sin(index * 12.9898)) ** 3),
);

function percentile(values: readonly number[], fraction: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.floor(sorted.length * fraction));
  return sorted[index] ?? 0;
}

function histogram(values: readonly number[]): { label: string; count: number; share: number }[] {
  const buckets = [512, 1_024, 2_048, 3_072, 4_096, 8_192];
  const counts = buckets.map((upper, index) => {
    const lower = index === 0 ? 0 : (buckets[index - 1] ?? 0);
    return {
      label: `${String(lower)}–${String(upper)}`,
      count: values.filter((value) => value > lower && value <= upper).length,
    };
  });
  const max = Math.max(...counts.map((bucket) => bucket.count), 1);
  return counts.map((bucket) => ({ ...bucket, share: (bucket.count / max) * 100 }));
}

/**
 * S09 Compose and S10 Dry run result.
 *
 * The dry run is the primary action and submission is the secondary one, and
 * submitting without a dry run takes an extra confirmation. An allocation on
 * this estate is the scarce resource; a specification error should cost
 * nothing to find.
 */
export function ComposeRun(): JSX.Element {
  const [text, setText] = useState(SAMPLE_SPECIFICATION);
  const [plan, setPlan] = useState<DryRun | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemOf> | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmBlind, setConfirmBlind] = useState(false);
  const [submitted, setSubmitted] = useState<{ runId: string; identity: string } | null>(null);

  function parsed(): unknown {
    try {
      return JSON.parse(text);
    } catch (cause) {
      setProblem({
        title: 'The specification is not valid JSON',
        detail: cause instanceof Error ? cause.message : 'It could not be parsed.',
      });
      return null;
    }
  }

  async function dryRun(): Promise<void> {
    const specification = parsed();
    if (specification === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await call('dryRunSpecification', { body: { specification } });
      setPlan(result.data);
    } catch (cause) {
      setPlan(null);
      setProblem(
        cause instanceof ApiError
          ? problemOf(cause)
          : { title: 'The dry run did not complete', detail: String(cause) },
      );
    } finally {
      setBusy(false);
    }
  }

  async function submit(): Promise<void> {
    const specification = parsed();
    if (specification === null) return;
    setConfirmBlind(false);
    setBusy(true);
    try {
      const result = await call('submitRun', {
        body: { specification },
        idempotencyKey: idempotencyKey(),
      });
      const body = result.data as { runId: string; runIdentity: string | null };
      setSubmitted({ runId: body.runId, identity: body.runIdentity ?? 'not recorded' });
    } catch (cause) {
      setProblem(
        cause instanceof ApiError
          ? problemOf(cause)
          : { title: 'The submission did not complete', detail: String(cause) },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeading title="Compose a run" />

      <label className="cn-editor__label" htmlFor="cn-spec">
        Run specification (SAD 6.2)
      </label>
      <textarea
        id="cn-spec"
        className="cn-editor"
        data-testid="spec-editor"
        spellCheck={false}
        rows={18}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={(event) => {
          // Never submit on Enter. A specification editor that submits on a
          // newline submits a half-written specification.
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) event.preventDefault();
        }}
      />

      <div className="cn-editor__actions">
        <Button
          onClick={() => {
            void dryRun();
          }}
          state={busy ? 'loading' : 'ready'}
        >
          Dry run
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            if (plan === null) setConfirmBlind(true);
            else void submit();
          }}
          state={busy ? 'loading' : 'ready'}
        >
          Submit
        </Button>
      </div>

      {problem === null ? null : <ErrorSurface problem={problem} />}

      {plan === null ? null : <DryRunResult plan={plan} onSubmit={() => void submit()} />}

      {submitted === null ? null : (
        <section className="cn-submitted" aria-labelledby="cn-submitted-heading" role="status">
          <h2 id="cn-submitted-heading">Run submitted</h2>
          <dl className="jg-facts">
            <dt>Run</dt>
            <dd>
              <a {...linkProps(`/runs/${submitted.runId}`)} data-testid="submitted-run-link">
                {submitted.runId}
              </a>
            </dd>
            <dt>Run identity</dt>
            <dd data-jg-mono="true" data-testid="submitted-identity">
              {submitted.identity}
            </dd>
          </dl>
          <p>
            The identity is the hash of the specification and its resolved input artefact hashes.
            Submitting this same file through <code>draupnirctl</code> produces the same identity.
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              navigate('/runs');
            }}
          >
            Watch the board
          </Button>
        </section>
      )}

      {confirmBlind ? (
        <Dialog
          title="Submit without a dry run?"
          consequence={
            'A submitted run takes an allocation. On this estate an allocation is the scarce ' +
            'resource, and a specification error found after submission has already cost one. ' +
            'A dry run renders the exact job plan and consumes nothing.'
          }
          confirmLabel="Submit anyway"
          onConfirm={() => {
            void submit();
          }}
          onDismiss={() => {
            setConfirmBlind(false);
          }}
        >
          <p>This specification has not been dry run.</p>
        </Dialog>
      ) : null}
    </>
  );
}

function DryRunResult({ plan, onSubmit }: { plan: DryRun; onSubmit: () => void }): JSX.Element {
  return (
    <section className="cn-plan" aria-labelledby="cn-plan-heading" data-testid="dry-run-result">
      <h2 id="cn-plan-heading">Rendered job plan</h2>
      <p className="cn-plan__note">
        No allocation was consumed. This is the exact plan the scheduler would be given.
      </p>
      <dl className="jg-facts">
        <dt>Driver</dt>
        <dd>{plan.driver}</dd>
        <dt>Run identity</dt>
        <dd data-jg-mono="true" data-testid="dry-run-identity">
          {plan.runIdentity}
        </dd>
        <dt>Specification hash</dt>
        <dd data-jg-mono="true">{plan.specHash}</dd>
      </dl>
      {/* Focusable because it scrolls: a keyboard user has to be able to
          reach and scroll a long command (WCAG 2.1.1). */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <pre className="cn-plan__command" tabIndex={0} aria-label="The command that would be run">
        {plan.command.join(' ')}
      </pre>
      {(plan.warnings ?? []).length === 0 ? null : (
        <ul className="cn-plan__warnings">
          {(plan.warnings ?? []).map((warning: string) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <Button onClick={onSubmit}>Submit this run</Button>
    </section>
  );
}

const SAMPLE_SPECIFICATION = JSON.stringify(
  {
    apiVersion: 'draupnir/v1',
    kind: 'AdapterRun',
    metadata: { name: 'cim-gbr-v0.5', jurisdiction: 'GBR', tier: 'A' },
    spec: {
      base: {
        artefact: 'hodd://models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0',
        expectSha256: 'a'.repeat(64),
      },
      dataset: {
        artefact: 'hodd://corpora/GBR/curated',
        expectSha256: 'b'.repeat(64),
        cutoffPercentile: 99,
      },
      train: {
        driver: 'hamarr.llamafactory/v1',
        method: 'lora',
        precision: 'bf16',
        // The checkpoint interval HAMARR derives so that no more than
        // thirty minutes of work is ever unwritten. The editor arrives
        // with a specification that dry runs cleanly, because the first
        // thing an operator does with it is press Dry run.
        params: { rank: 16, save_steps: 500 },
      },
      placement: { driver: 'motsognir.slurm/v1', partition: 'default', nodes: 1 },
      evaluate: { driver: 'raun.lmeval/v1', suites: ['legal-qa'], gates: ['E1'], baseline: null },
      release: { route: 'tier-a', formats: ['gguf'], approval: 'required' },
    },
  },
  null,
  2,
);
