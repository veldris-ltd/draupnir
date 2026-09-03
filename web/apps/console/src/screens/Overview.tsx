import type { JSX } from 'react';
import { Badge, CapacityGauge, Table } from '@draupnir/jarngreipr';
import type { Site } from '@draupnir/api-client';
import { pageIsEmpty, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { PageHeading } from './parts';

/**
 * S01 Overview and S22 Sites.
 *
 * The overview answers one question -- what needs attention -- and every
 * number on it is scoped to this site. There is deliberately no "all sites"
 * total anywhere: AC-U11 forbids an unscoped aggregate view, and a summary
 * card is exactly where one would arrive without anyone deciding to build it.
 */

export function Overview(): JSX.Element {
  const runs = useResource('listRuns', { query: { limit: 50 } });
  const gates = useResource('listGates', { query: { limit: 50, state: 'pending' } });
  const health = useResource('getHealth', {});

  const items = runs.data?.items ?? [];
  const failed = items.filter((run) => run.state === 'FAILED' || run.state === 'QUARANTINED');
  const training = items.filter((run) => run.state === 'TRAINING' || run.state === 'EVALUATING');
  const waiting = gates.data?.items.length ?? 0;

  return (
    <>
      <PageHeading
        title="Overview"
        subtitle={health.data ? `Site ${health.data.siteId}` : 'Site not yet known'}
      />

      <div className="cn-cards">
        <a className="cn-card" {...linkProps('/runs?state=FAILED')}>
          <span className="cn-card__figure">{failed.length}</span>
          <span className="cn-card__label">runs needing attention</span>
        </a>
        <a className="cn-card" {...linkProps('/runs')}>
          <span className="cn-card__figure">{training.length}</span>
          <span className="cn-card__label">runs in progress</span>
        </a>
        <a className="cn-card" {...linkProps('/gates')}>
          <span className="cn-card__figure">{waiting}</span>
          <span className="cn-card__label">artefacts awaiting a decision</span>
        </a>
      </div>

      <CapacityGauge
        label="Runs occupying this site"
        used={training.length}
        total={Math.max(items.length, 1)}
        unit="runs"
        state={runs.state === 'ready' ? 'ready' : runs.state}
      />
    </>
  );
}

export function Sites(): JSX.Element {
  const sites = useResource('listSites', { emptyWhen: pageIsEmpty });

  const columns = [
    { key: 'name', header: 'Site', render: (row: Site) => row.name },
    { key: 'location', header: 'Location', render: (row: Site) => row.location },
    {
      key: 'anchor',
      header: 'Federation anchor',
      render: (row: Site) => (
        <Badge
          tone={
            row.anchorState === 'ANCHORED'
              ? 'success'
              : row.anchorState === 'PARTITIONED'
                ? 'warning'
                : 'neutral'
          }
        >
          {row.anchorState}
        </Badge>
      ),
    },
    {
      key: 'anchored_at',
      header: 'Last countersigned',
      render: (row: Site) =>
        row.lastAnchoredAt == null ? 'never' : new Date(row.lastAnchoredAt).toLocaleString(),
    },
    {
      key: 'uri',
      header: 'Control plane',
      render: (row: Site) => <code className="cn-digest">{row.controlPlaneUri}</code>,
    },
  ];

  return (
    <>
      <PageHeading
        title="Sites"
        subtitle="The Forge Matrix. One site is one forge; an appliance within it is something else."
      />
      <p className="cn-note">
        This is the registry of sites, not an aggregate across them. Each forge&rsquo;s runs,
        corpora and ledger are read from its own control plane, which is what the switcher in the
        header changes.
      </p>
      <Table
        caption="Registered sites"
        columns={columns}
        rows={sites.data?.items ?? []}
        rowKey={(row) => row.id}
        state={sites.state}
        problem={sites.problem}
      />
    </>
  );
}
