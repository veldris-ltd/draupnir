import type { JSX } from 'react';
import { useState } from 'react';
import { Badge, Button, Drawer, StateSurface } from '@draupnir/jarngreipr';
import { pageIsEmpty, useResource } from '../api/useResource';
import { linkProps } from '../routing';

/**
 * The alert tray. SAD 11.3's alarms, where somebody will see them.
 *
 * The worker records an alarm as a ledger entry against the site --
 * `alarm.raised`, with the duty that raised it and what it measured -- because
 * an alarm is a fact somebody will later need to establish. That makes the
 * chain the source here too: the tray is a read of the ledger, not a second
 * store of alerts that could disagree with it.
 *
 * **Not a notification.** It does not pop, it does not interrupt, and it does
 * not clear itself. An alarm on this estate means a chain that stopped
 * verifying or a vault at 85 per cent, and neither is a thing to dismiss on
 * the way past. The count is in the header; the detail is a drawer somebody
 * opens deliberately.
 *
 * **Nothing is optimistic.** The count is what the ledger holds at the last
 * read, and the tray says when that was. A tray that showed what it expected
 * the ledger to hold would be the one thing on this screen that could be
 * wrong about the chain.
 */

/** The transition an alarm carries. Matches `draupnir/worker/duties.py`. */
const ALARM = 'alarm.raised';

interface LedgerRow {
  seq: number;
  ts: string;
  transition: string;
  subjectId: string;
  payload?: { duty?: string; detail?: string } | undefined;
}

export function AlertTray({ siteId }: { siteId: string | null }): JSX.Element {
  const [open, setOpen] = useState(false);

  // The last hundred entries, filtered here rather than by a query the API
  // does not offer. A tray that asked for a filter the server cannot do would
  // be a tray that silently showed the first page of everything.
  const ledger = useResource('getLedger', { query: { limit: 100 }, emptyWhen: pageIsEmpty });
  const rows = ((ledger.data?.items ?? []) as LedgerRow[]).filter(
    (row) => row.transition === ALARM,
  );

  const count = rows.length;

  return (
    <>
      <Button
        variant={count === 0 ? 'ghost' : 'secondary'}
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        Alerts
        {count === 0 ? null : (
          <>
            {' '}
            <Badge tone="warning">{String(count)}</Badge>
          </>
        )}
      </Button>
      {/*
       * The count in words as well as in a badge. A screen reader user hears
       * "Alerts 3" from the badge alone with no idea what the 3 counts.
       */}
      <span className="jg-sr-only">
        {count === 0
          ? 'No alarms are recorded for this site.'
          : `${String(count)} ${count === 1 ? 'alarm is' : 'alarms are'} recorded for this site.`}
      </span>

      {open ? (
        <Drawer
          title={siteId === null ? 'Alerts' : `Alerts at ${siteId}`}
          onClose={() => {
            setOpen(false);
          }}
        >
          <StateSurface
            label="Alerts"
            state={ledger.state === 'ready' && count === 0 ? 'empty' : ledger.state}
            problem={ledger.problem}
            stateMessage={
              ledger.state === 'ready'
                ? 'No alarm has been recorded for this site. The periodic duties record one only when a threshold is crossed, so an empty tray is the ordinary state.'
                : undefined
            }
            reserve="md"
          >
            <ul className="cn-alerts">
              {rows.map((row) => (
                <li key={row.seq} className="cn-alerts__item">
                  <p className="cn-alerts__duty">{row.payload?.duty ?? 'alarm'}</p>
                  <p>{row.payload?.detail ?? 'No detail was recorded.'}</p>
                  <p className="cn-alerts__meta">
                    <span>{row.ts}</span>
                    {' · '}
                    <a {...linkProps(`/audit?seq=${String(row.seq)}`)}>
                      Ledger entry {String(row.seq)}
                    </a>
                  </p>
                </li>
              ))}
            </ul>
          </StateSurface>
        </Drawer>
      ) : null}
    </>
  );
}
