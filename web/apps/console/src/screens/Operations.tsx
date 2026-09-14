import type { JSX } from 'react';
import { useState } from 'react';
import {
  Badge,
  Button,
  CapacityGauge,
  Dialog,
  StateSurface,
  SweepMatrix,
  Table,
  type ComponentState,
} from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey } from '@draupnir/api-client';
import { pageIsEmpty, stateForError, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { PageHeading } from './parts';

/**
 * S12 Array monitor and S15 Sweep comparison.
 *
 * Both are operator screens about work in flight rather than about a single
 * run, and both exist because the fifty-six element array is the unit the
 * factory actually produces. A board that only shows runs makes an operator
 * reconstruct the array in their head.
 */

interface Element {
  index: number;
  subject: string;
  state: string;
  attempts: number;
  runId?: string | null;
  node?: string | null;
}

const ELEMENT_TONE: Record<string, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  PENDING: 'neutral',
  RUNNING: 'info',
  COMPLETED: 'success',
  FAILED: 'danger',
  AWAITING_RETRY: 'warning',
  EXHAUSTED: 'danger',
  CANCELLED: 'neutral',
};

/**
 * The element states a requeue is for: an element that stopped without
 * completing (RF-27). The same set as `motsognir.arrays.REQUEUEABLE`, and the
 * worker refuses the others as well -- a request can reach the API without
 * this screen, and a completed element requeued is an adapter trained twice.
 */
const REQUEUEABLE = new Set(['FAILED', 'AWAITING_RETRY', 'EXHAUSTED', 'CANCELLED']);

/**
 * S12. The array, element by element.
 *
 * The element vocabulary is deliberately not the run vocabulary. An element
 * that failed inside its retry budget is `AWAITING_RETRY` — neither running
 * nor finished — and collapsing that into `FAILED` loses the budget, which is
 * the one number an operator needs to decide whether to intervene.
 *
 * Its primary action is requeueing one element (RF-27). `requeueArrayElement`
 * existed from RF-13 and nothing on this screen called it, so the action the
 * UX inventory names for S12 could not be performed from S12.
 */
export function ArrayMonitor(): JSX.Element {
  const array = useResource('getArray', { query: { limit: 100 } });
  const data = array.data;
  const elements = (data?.elements ?? []) as Element[];
  const summary = data?.summary ?? {};
  const [requeueing, setRequeueing] = useState<Element | null>(null);
  const [outcome, setOutcome] = useState<{ state: ComponentState; message: string } | null>(null);

  const completed = summary.COMPLETED ?? 0;
  const size = data?.size ?? 0;

  async function requeue(element: Element): Promise<void> {
    setRequeueing(null);
    setOutcome({ state: 'loading', message: `Requeueing element ${String(element.index)}.` });
    try {
      await call('requeueArrayElement', {
        params: { name: data?.name ?? '', index: String(element.index) },
        idempotencyKey: idempotencyKey(),
      });
      // Accepted, not done. The worker performs the requeue and records the
      // outcome against the array -- and at a site whose scheduler transport
      // cannot requeue, the outcome is a refusal naming what to run instead.
      // Saying "requeued" here would claim a result nobody has seen yet.
      setOutcome({
        state: 'ready',
        message:
          `Requeue of element ${String(element.index)} (${element.subject}) accepted. The ` +
          'worker performs it and records the outcome against the array; where the scheduler ' +
          'cannot requeue, that record says what to run instead.',
      });
    } catch (cause) {
      setOutcome({
        state: cause instanceof ApiError ? stateForError(cause) : 'error',
        message: cause instanceof ApiError ? cause.problem.title : 'The requeue did not complete.',
      });
    }
  }

  const columns = [
    { key: 'index', header: '#', numeric: true, render: (row: Element) => row.index },
    { key: 'subject', header: 'Element', render: (row: Element) => row.subject },
    {
      key: 'state',
      header: 'State',
      render: (row: Element) => (
        <Badge tone={ELEMENT_TONE[row.state] ?? 'neutral'}>{row.state}</Badge>
      ),
    },
    {
      key: 'attempts',
      header: 'Attempts',
      numeric: true,
      render: (row: Element) => row.attempts,
    },
    { key: 'node', header: 'Node', render: (row: Element) => row.node ?? '—' },
    {
      key: 'run',
      header: 'Run',
      render: (row: Element) =>
        row.runId == null ? (
          <>
            <span aria-hidden="true">—</span>
            <span className="jg-sr-only">Not started</span>
          </>
        ) : (
          <a {...linkProps(`/runs/${row.runId}`)}>open</a>
        ),
    },
    {
      key: 'action',
      header: 'Action',
      render: (row: Element) =>
        REQUEUEABLE.has(row.state) ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setRequeueing(row);
            }}
          >
            {`Requeue element ${String(row.index)}`}
          </Button>
        ) : (
          <span className="jg-sr-only">Nothing to requeue</span>
        ),
    },
  ];

  return (
    <>
      <PageHeading title="Array" subtitle={data?.name ?? 'The adapter array'} />

      <p className="cn-note">
        One element per jurisdiction. An element that has not started is <code>PENDING</code>, which
        is a state rather than a missing row: the monitor exists to show what has not begun as much
        as what has.
      </p>

      <CapacityGauge
        label="Elements completed"
        used={completed}
        total={Math.max(size, 1)}
        unit="elements"
        state={array.state === 'ready' ? 'ready' : array.state}
      />

      <ul className="cn-summary" aria-label="Element states">
        {Object.entries(summary).map(([state, count]) => (
          <li key={state}>
            <Badge tone={ELEMENT_TONE[state] ?? 'neutral'}>
              {count} {state}
            </Badge>
          </li>
        ))}
      </ul>

      <Table
        caption="Every element of the array, in index order"
        columns={columns}
        rows={elements}
        rowKey={(row) => String(row.index)}
        state={elements.length === 0 && array.state === 'ready' ? 'empty' : array.state}
        problem={array.problem}
      />

      {outcome === null ? null : (
        <p
          className="cn-action-result"
          role="status"
          data-testid="requeue-result"
          data-jg-state={outcome.state}
        >
          {outcome.message}
        </p>
      )}

      {requeueing === null ? null : (
        <Dialog
          title={`Requeue element ${String(requeueing.index)}?`}
          consequence={
            `Element ${String(requeueing.index)} (${requeueing.subject}) goes back on the ` +
            'scheduler queue as the same element of the same array. The other ' +
            `${String(Math.max(size - 1, 0))} are not touched. It holds an appliance for as ` +
            'long as the adapter trains.'
          }
          confirmLabel="Requeue the element"
          onConfirm={() => {
            void requeue(requeueing);
          }}
          onDismiss={() => {
            setRequeueing(null);
          }}
        >
          <p>
            {requeueing.state} after {requeueing.attempts}{' '}
            {requeueing.attempts === 1 ? 'attempt' : 'attempts'}, on{' '}
            {requeueing.node ?? 'no recorded node'}.
          </p>
        </Dialog>
      )}
    </>
  );
}

/**
 * S15. The reweighting trade.
 *
 * "The reweighting decision is a trade, and the screen presents it as one."
 * The sentence beneath the matrix is generated from the data by the API rather
 * than written here, because a hard-coded sentence stops being true the first
 * time the numbers move — and the entire reason for having one is that twenty
 * numbers do not by themselves tell an operator that the higher scoring points
 * fail a different gate.
 */
export function SweepComparison({ runId }: { runId: string }): JSX.Element {
  const sweep = useResource('getSweep', { params: { run_id: runId } });
  const data = sweep.data;

  const metrics = (data?.gates ?? []).map((gate: string) => ({
    key: gate,
    label: gate,
    higherIsBetter: true,
  }));
  const arms = (data?.points ?? []).map(
    (point: { label: string; scores?: Record<string, number>; passed: boolean }) => ({
      id: point.label,
      label: `${point.label}${point.passed ? '' : ' (fails a floor)'}`,
      values: point.scores ?? {},
    }),
  );

  return (
    <>
      <PageHeading title="Sweep" subtitle={data?.model ?? runId} />

      <StateSurface
        state={sweep.state}
        problem={sweep.problem}
        label="Sweep comparison"
        reserve="xl"
      >
        {data === undefined ? null : (
          <>
            <SweepMatrix
              caption={`Merge points against gates, ${data.model}`}
              metrics={metrics}
              arms={arms}
            />

            <p className="cn-trade" data-testid="sweep-trade">
              {data.trade}
            </p>

            {data.selected == null ? null : (
              <p className="cn-note" data-testid="sweep-selected">
                Selected point: <strong>{data.selected}</strong>. Every floor is cleared.
              </p>
            )}

            <section aria-labelledby="cn-floors-heading">
              <h2 id="cn-floors-heading">Floors</h2>
              <p className="cn-note">
                A point below any of these cannot be released whatever it scores elsewhere.
              </p>
              <dl className="jg-facts" data-testid="sweep-floors">
                {Object.entries(data.floors ?? {}).map(([gate, floor]) => (
                  <div key={gate}>
                    <dt>{gate}</dt>
                    <dd>{String(floor)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </>
        )}
      </StateSurface>
    </>
  );
}

/** S23. Installed plug-ins and their signature status. */
export function Plugins(): JSX.Element {
  const plugins = useResource('listPlugins', { emptyWhen: pageIsEmpty });
  const failures = plugins.data?.failures ?? [];

  interface Plugin {
    name: string;
    group: string;
    distribution: string;
    version: string;
    capabilities: string[];
    signatureVerified: boolean;
    signer?: string | null;
    reason?: string | null;
  }

  const columns = [
    { key: 'name', header: 'Plug-in', render: (row: Plugin) => row.name },
    { key: 'group', header: 'Extension point', render: (row: Plugin) => row.group },
    {
      key: 'version',
      header: 'Version',
      render: (row: Plugin) => `${row.distribution} ${row.version}`,
    },
    {
      key: 'signature',
      header: 'Signature',
      render: (row: Plugin) =>
        row.signatureVerified ? (
          <Badge tone="success">verified{row.signer == null ? '' : ` by ${row.signer}`}</Badge>
        ) : (
          <Badge tone="danger">
            not verified
            <span className="jg-sr-only">. {row.reason ?? 'no reason recorded'}</span>
          </Badge>
        ),
    },
    {
      key: 'capabilities',
      header: 'Capabilities',
      render: (row: Plugin) => row.capabilities.join(', '),
    },
  ];

  return (
    <>
      <PageHeading title="Plug-ins" subtitle="What loaded, and what was refused." />

      <p className="cn-note">
        An unsigned plug-in fails to load (AC-S7). The refusals below are the loader working; a
        driver that is missing from a run is usually a driver that is listed here.
      </p>

      {failures.length === 0 ? null : (
        <section className="cn-error" role="status" aria-labelledby="cn-refused-heading">
          <h2 className="cn-error__title" id="cn-refused-heading">
            {failures.length} distribution{failures.length === 1 ? '' : 's'} refused
          </h2>
          <ul data-testid="plugin-failures">
            {failures.map((failure: string) => (
              <li key={failure}>{failure}</li>
            ))}
          </ul>
        </section>
      )}

      <Table
        caption="Installed plug-ins"
        columns={columns}
        rows={plugins.data?.items ?? []}
        rowKey={(row) => `${row.name}:${row.version}`}
        state={plugins.state}
        problem={plugins.problem}
      />
    </>
  );
}
