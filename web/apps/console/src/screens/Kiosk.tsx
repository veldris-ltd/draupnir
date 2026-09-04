import type { JSX } from 'react';
import { useEffect, useState } from 'react';
import { Badge, CapacityGauge, StateSurface, Table } from '@draupnir/jarngreipr';
import { useResource } from '../api/useResource';
import { freshnessSentence, useEvents } from '../api/useEvents';
import { OPERATIONS, urlFor } from '@draupnir/api-client';
import type { Run } from '@draupnir/api-client';

/**
 * S31 CON-B, the operations dashboard, in kiosk mode.
 *
 * Three dashboards on a 1280×720 panel driven by REGIN: thermal, fabric and
 * queue (SAD 11.2). It is read only and has no navigation, because nobody
 * stands at it — it is on a wall.
 *
 * Two things distinguish it from the console proper and both come from that.
 *
 * **It is not CON-A.** CON-B reads the API and is useless without it, which is
 * fine: it is driven by REGIN and watches the estate. CON-A is attached to
 * DVALIN and exists to survive the API being gone (Decision U2). Sharing a code
 * path between the two is exactly how CON-A would acquire a dependency, so
 * CON-A is a separate package in `tools/stedi-view/` and this is not.
 *
 * **It states its own staleness.** A wall panel showing numbers that stopped
 * updating an hour ago is worse than a blank one: nobody is looking at it
 * closely enough to notice, and the numbers are believed. So the freshness is
 * on the panel in words, and it rotates through the three dashboards on a timer
 * rather than waiting for an interaction that will never come.
 *
 * AC-S16 asks it to recover automatically after an appliance restart. There is
 * nothing to recover: it holds no state, takes no input, and the event stream
 * reconnects itself. A restart is a page load.
 */

const DASHBOARDS = ['thermal', 'fabric', 'queue'] as const;
type Dashboard = (typeof DASHBOARDS)[number];

/** How long each dashboard holds the panel. */
const ROTATE_SECONDS = 20;

export function KioskDashboard(): JSX.Element {
  const [dashboard, setDashboard] = useState<Dashboard>('thermal');
  const runs = useResource('listRuns', { query: { limit: 100 } });
  const sites = useResource('listSites', {});
  const health = useResource('getHealth', {});
  const feed = useEvents(urlFor(OPERATIONS.streamSiteEvents));

  useEffect(() => {
    const timer = window.setInterval(() => {
      setDashboard((current) => {
        const next = (DASHBOARDS.indexOf(current) + 1) % DASHBOARDS.length;
        return DASHBOARDS[next] ?? 'thermal';
      });
    }, ROTATE_SECONDS * 1_000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const items = runs.data?.items ?? [];
  const here = sites.data?.items.find((site) => site.id === health.data?.siteId);

  return (
    <div className="cn-kiosk">
      <header className="cn-kiosk__header">
        <h1>{here?.name ?? health.data?.siteId ?? 'DRAUPNIR'}</h1>
        <nav className="cn-kiosk__tabs" aria-label="Dashboards">
          {DASHBOARDS.map((name) => (
            <span
              key={name}
              className="cn-kiosk__tab"
              data-jg-active={name === dashboard ? 'true' : undefined}
              aria-current={name === dashboard ? 'true' : undefined}
            >
              {name}
            </span>
          ))}
        </nav>
      </header>

      <StateSurface
        state={runs.state === 'ready' ? 'ready' : runs.state}
        problem={runs.problem}
        label="Operations dashboard"
        reserve="xl"
      >
        {dashboard === 'thermal' ? <Thermal runs={items} /> : null}
        {dashboard === 'fabric' ? <Fabric anchorState={here?.anchorState} /> : null}
        {dashboard === 'queue' ? <Queue runs={items} /> : null}
      </StateSurface>

      <footer className="cn-kiosk__footer">
        {/* The staleness, in words. A wall panel whose numbers stopped an hour
            ago is worse than a blank one: nobody is watching closely enough to
            notice, and the numbers are believed. */}
        <span data-testid="kiosk-freshness">{freshnessSentence(feed)}</span>
        <span>Read only. No control on this panel.</span>
      </footer>
    </div>
  );
}

/** Dashboard 1: appliance thermal and throttle. */
function Thermal({ runs }: { runs: readonly Run[] }): JSX.Element {
  const nodes = [
    ...new Set(runs.map((run) => run.node).filter((node): node is string => node != null)),
  ];
  const busy = new Set(
    runs
      .filter((run) => run.state === 'TRAINING' || run.state === 'EVALUATING')
      .map((run) => run.node),
  );

  return (
    <section aria-labelledby="cn-thermal-heading" className="cn-kiosk__panel">
      <h2 id="cn-thermal-heading">Appliances</h2>
      {nodes.length === 0 ? (
        <p className="cn-note">
          No appliance is placed. Thermal and throttle readings come from the DCGM exporter on each
          appliance; with nothing placed there is nothing to report, which is not the same as
          everything being cool.
        </p>
      ) : (
        <ul className="cn-kiosk__grid">
          {nodes.map((node) => (
            <li key={node} className="cn-kiosk__tile">
              <span className="cn-kiosk__tile-name">{node}</span>
              <Badge tone={busy.has(node) ? 'info' : 'neutral'}>
                {busy.has(node) ? 'under load' : 'idle'}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Dashboard 2: fabric and the federation link. */
function Fabric({ anchorState }: { anchorState: string | undefined }): JSX.Element {
  const partitioned = anchorState === 'PARTITIONED';
  return (
    <section aria-labelledby="cn-fabric-heading" className="cn-kiosk__panel">
      <h2 id="cn-fabric-heading">Fabric and federation</h2>
      <p className="cn-kiosk__figure" data-testid="kiosk-anchor">
        {anchorState ?? 'unknown'}
      </p>
      <p>
        {partitioned
          ? 'Partitioned. Training and evaluation continue; release is unavailable until the link returns.'
          : 'The chain head is countersigned by another site.'}
      </p>
      <p className="cn-note">
        The fabric bandwidth probe alarms below 80 per cent of the commissioned baseline. It runs
        hourly and is dispatched by MOTSOGNIR, so a silent probe is itself a finding.
      </p>
    </section>
  );
}

/** Dashboard 3: run state and queue depth. */
function Queue({ runs }: { runs: readonly Run[] }): JSX.Element {
  const queued = runs.filter((run) => run.state === 'QUEUED' || run.state === 'DRAFT');
  const running = runs.filter((run) => run.state === 'TRAINING' || run.state === 'EVALUATING');
  const failed = runs.filter((run) => run.state === 'FAILED' || run.state === 'QUARANTINED');

  return (
    <section aria-labelledby="cn-queue-heading" className="cn-kiosk__panel">
      <h2 id="cn-queue-heading">Queue</h2>

      <ul className="cn-kiosk__grid">
        <li className="cn-kiosk__tile">
          <span className="cn-kiosk__figure">{running.length}</span>
          <span className="cn-kiosk__tile-name">running</span>
        </li>
        <li className="cn-kiosk__tile">
          <span className="cn-kiosk__figure">{queued.length}</span>
          <span className="cn-kiosk__tile-name">queued</span>
        </li>
        <li className="cn-kiosk__tile">
          <span className="cn-kiosk__figure">{failed.length}</span>
          <span className="cn-kiosk__tile-name">needing attention</span>
        </li>
      </ul>

      <CapacityGauge
        label="Runs in flight"
        used={running.length}
        total={Math.max(runs.length, 1)}
        unit="runs"
      />

      <Table
        caption="Runs needing attention"
        columns={[
          { key: 'name', header: 'Run', render: (row: Run) => row.name },
          { key: 'state', header: 'State', render: (row: Run) => row.state },
          { key: 'node', header: 'Node', render: (row: Run) => row.node ?? '—' },
        ]}
        rows={failed.slice(0, 6)}
        rowKey={(row) => row.id}
        state={failed.length === 0 ? 'empty' : 'ready'}
        stateMessage={failed.length === 0 ? 'Nothing needs attention.' : undefined}
      />
    </section>
  );
}
