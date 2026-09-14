import type { JSX } from 'react';
import { useEffect, useState } from 'react';
import { Badge, CapacityGauge, StateSurface, Table } from '@draupnir/jarngreipr';
import { useResource } from '../api/useResource';
import { freshnessSentence, useEvents } from '../api/useEvents';
import { OPERATIONS, urlFor } from '@draupnir/api-client';
import type { ResponseOf, Run } from '@draupnir/api-client';

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

/** SAD 11.3: the fabric alarms below 80 per cent of the commissioned baseline. */
const FABRIC_FLOOR = 0.8;

/**
 * The dashboard a link asks for, if it names one (RF-28).
 *
 * The wall rotates, and nothing looking at it needs to choose. A link does: an
 * operator sending "the fabric looks slow" wants to send the fabric, and a
 * scan or a journey that had to wait out the rotation would be timing a wall
 * clock. A named dashboard holds; an unnamed one rotates as it always has.
 */
function linkedDashboard(): Dashboard | null {
  const asked = new URLSearchParams(window.location.search).get('dashboard');
  return DASHBOARDS.find((name) => name === asked) ?? null;
}

export function KioskDashboard(): JSX.Element {
  const [linked] = useState<Dashboard | null>(linkedDashboard);
  const [dashboard, setDashboard] = useState<Dashboard>(linked ?? 'thermal');
  const runs = useResource('listRuns', { query: { limit: 100 } });
  const sites = useResource('listSites', {});
  const health = useResource('getHealth', {});
  // The thermal and fabric numbers, read back out of the estate's own
  // collector. Its own read rather than a field on another one: it comes from
  // Prometheus rather than the database, it fails on its own schedule, and a
  // failure here must leave the queue dashboard rendering.
  const estate = useResource('getEstateTelemetry', {});
  const feed = useEvents(urlFor(OPERATIONS.streamSiteEvents));

  useEffect(() => {
    if (linked !== null) return;
    const timer = window.setInterval(() => {
      setDashboard((current) => {
        const next = (DASHBOARDS.indexOf(current) + 1) % DASHBOARDS.length;
        return DASHBOARDS[next] ?? 'thermal';
      });
    }, ROTATE_SECONDS * 1_000);
    return () => {
      window.clearInterval(timer);
    };
  }, [linked]);

  const items = runs.data?.items ?? [];
  const here = sites.data?.items.find((site) => site.id === health.data?.siteId);
  const readings = estate.data?.readings ?? [];

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
        {dashboard === 'thermal' ? <Thermal readings={readings} /> : null}
        {dashboard === 'fabric' ? (
          <Fabric anchorState={here?.anchorState} readings={readings} />
        ) : null}
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

/** One reading from the estate's collector: a number, or why there is not one. */
type Reading = ResponseOf<'getEstateTelemetry'>['readings'][number];

/** The reading of one metric for one subject, if the collector had one. */
function readingFor(
  readings: readonly Reading[],
  metric: string,
  subject: string,
): Reading | undefined {
  return readings.find((item) => item.metric === metric && item.subject === subject);
}

/**
 * How a reading renders.
 *
 * Never a zero and never a green tile where there is no measurement. A panel
 * showing `0` in green is worse than one saying it does not know, because the
 * first is believed — and nobody stands close enough to a wall panel to
 * check. So an absent reading renders the word `unmeasured` and carries the
 * collector's reason with it.
 */
function Measured({ reading, unit }: { reading: Reading | undefined; unit: string }): JSX.Element {
  if (reading?.value == null) {
    return (
      <span
        className="cn-kiosk__figure"
        data-measured="false"
        data-testid="unmeasured"
        title={reading?.reason ?? 'No reading was returned.'}
      >
        unmeasured
      </span>
    );
  }
  return (
    <span className="cn-kiosk__figure" data-measured="true">
      {reading.value}
      {unit}
    </span>
  );
}

/** Dashboard 1: appliance thermal and throttle. */
function Thermal({ readings }: { readings: readonly Reading[] }): JSX.Element {
  // The appliances the collector was asked about, in the order it answered.
  // Every appliance is present whether or not it had a reading: a panel that
  // omitted an unreachable machine would look exactly like a smaller estate.
  const nodes = [
    ...new Set(
      readings.filter((item) => item.metric === 'gpu_temperature').map((item) => item.subject),
    ),
  ];

  return (
    <section aria-labelledby="cn-thermal-heading" className="cn-kiosk__panel">
      <h2 id="cn-thermal-heading">Appliances</h2>
      {nodes.length === 0 ? (
        <p className="cn-note" data-testid="thermal-unmeasured">
          Unmeasured. Thermal and throttle readings come from the DCGM exporter on each appliance,
          read back out of the site&rsquo;s Prometheus; nothing answered. That is not the same as
          everything being cool.
        </p>
      ) : (
        <ul className="cn-kiosk__grid">
          {nodes.map((node) => {
            const temperature = readingFor(readings, 'gpu_temperature', node);
            const throttle = readingFor(readings, 'throttle_reasons', node);
            return (
              <li key={node} className="cn-kiosk__tile" data-testid={`thermal-${node}`}>
                <span className="cn-kiosk__tile-name">{node}</span>
                <Measured reading={temperature} unit=" °C" />
                {/* A bitmask of zero is a real reading and means nothing is
                    throttling, which is exactly why an unavailable one must
                    not also render as zero. */}
                <Badge tone={throttleTone(throttle)}>{throttleWord(throttle)}</Badge>
                {temperature != null && temperature.value == null && temperature.reason != null ? (
                  <span className="cn-note">{temperature.reason}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function throttleWord(reading: Reading | undefined): string {
  if (reading?.value == null) return 'throttle unmeasured';
  return reading.value === 0 ? 'not throttling' : 'throttling';
}

function throttleTone(reading: Reading | undefined): 'info' | 'neutral' | 'warning' {
  if (reading?.value == null) return 'neutral';
  return reading.value === 0 ? 'info' : 'warning';
}

/** Dashboard 2: fabric and the federation link. */
function Fabric({
  anchorState,
  readings,
}: {
  anchorState: string | undefined;
  readings: readonly Reading[];
}): JSX.Element {
  const partitioned = anchorState === 'PARTITIONED';
  const bandwidth = readingFor(readings, 'fabric_bandwidth', 'baugr');
  const baseline = readingFor(readings, 'fabric_baseline', 'baugr');
  // The reading against the baseline, which is what SAD 11.3 surfaces (RF-28).
  // A bandwidth alone cannot say whether the fabric is healthy: 190 GB/s is a
  // good day on one estate and a failing cable on another.
  const fraction =
    bandwidth?.value != null && baseline?.value != null && baseline.value > 0
      ? bandwidth.value / baseline.value
      : null;
  return (
    <section aria-labelledby="cn-fabric-heading" className="cn-kiosk__panel">
      <h2 id="cn-fabric-heading">Fabric and federation</h2>

      <div className="cn-kiosk__tile" data-testid="kiosk-bandwidth">
        <span className="cn-kiosk__tile-name">BAUGR bus bandwidth</span>
        <Measured reading={bandwidth} unit=" GB/s" />
        {bandwidth != null && bandwidth.value == null && bandwidth.reason != null ? (
          <span className="cn-note">{bandwidth.reason}</span>
        ) : null}
        <span data-testid="kiosk-baseline">
          commissioned baseline <Measured reading={baseline} unit=" GB/s" />
        </span>
        {fraction !== null ? (
          <Badge tone={fraction < FABRIC_FLOOR ? 'warning' : 'info'}>
            <span data-testid="kiosk-fraction">
              {`${String(Math.round(fraction * 100))} per cent of baseline`}
              {fraction < FABRIC_FLOOR ? ', below the 80 per cent floor' : ''}
            </span>
          </Badge>
        ) : bandwidth?.value != null ? (
          <span className="cn-note" data-testid="kiosk-baseline-unmeasured">
            No commissioned baseline is recorded, so the 80 per cent alarm cannot be judged.
            {baseline?.reason != null ? ` ${baseline.reason}` : ''}
          </span>
        ) : null}
      </div>

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
