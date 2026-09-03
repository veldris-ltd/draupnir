import type { JSX } from 'react';
import { Badge, Button, Dialog, Table } from '@draupnir/jarngreipr';
import { useState } from 'react';
import { pageIsEmpty, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { PageHeading } from './parts';

/**
 * S05 Curation run and S06 Retention schedule.
 *
 * Both screens are about a corpus's lifecycle rather than a run's, which is
 * why they sit under Corpora and not under Runs. The distinction matters:
 * SAD 6.1 gives `CORPUS_REGISTERED` and `LICENCE_CLEARED` to a corpus awaiting
 * a judgement, and no run ever rests in them.
 */

interface Corpus {
  jurisdiction: string;
  sources: number;
  curated: number;
  quarantined: number;
  awaiting: number;
  personalDataSources: number;
  missingDpia: number;
  licences?: string[];
  latestRetrieval?: string | null;
}

/**
 * S05. Stage by stage, per jurisdiction.
 *
 * A quarantined source is counted beside the others rather than rendered as an
 * error, because it is the licence gate working. GLEIPNIR judged the licence
 * and refused it; that is an outcome, not a fault, and a screen that showed it
 * in red would teach a curator to treat a correct refusal as something to fix.
 */
export function CurationRuns(): JSX.Element {
  const corpora = useResource('listCorpora', { emptyWhen: pageIsEmpty });

  const columns = [
    {
      key: 'jurisdiction',
      header: 'Jurisdiction',
      render: (row: Corpus) => (
        <a {...linkProps(`/corpora?jurisdiction=${row.jurisdiction}`)}>{row.jurisdiction}</a>
      ),
    },
    { key: 'sources', header: 'Sources', numeric: true, render: (row: Corpus) => row.sources },
    {
      key: 'curated',
      header: 'Curated',
      numeric: true,
      render: (row: Corpus) => (
        <Badge tone={row.curated === row.sources ? 'success' : 'info'}>
          {row.curated} of {row.sources}
        </Badge>
      ),
    },
    {
      key: 'awaiting',
      header: 'Awaiting',
      numeric: true,
      render: (row: Corpus) => row.awaiting,
    },
    {
      key: 'quarantined',
      header: 'Quarantined',
      numeric: true,
      render: (row: Corpus) =>
        row.quarantined === 0 ? (
          <>
            <span aria-hidden="true">—</span>
            <span className="jg-sr-only">None quarantined</span>
          </>
        ) : (
          <Badge tone="warning">
            {row.quarantined}
            <span className="jg-sr-only"> refused by the licence gate</span>
          </Badge>
        ),
    },
    {
      key: 'personal',
      header: 'Personal data',
      numeric: true,
      render: (row: Corpus) =>
        row.missingDpia > 0 ? (
          <Badge tone="danger">
            {row.missingDpia} with no DPIA
            <span className="jg-sr-only">
              . This should be impossible: the database refuses such a row.
            </span>
          </Badge>
        ) : (
          <span>{row.personalDataSources}</span>
        ),
    },
    {
      key: 'licences',
      header: 'Licences',
      render: (row: Corpus) => (row.licences ?? []).join(', '),
    },
  ];

  return (
    <>
      <PageHeading title="Curation" subtitle="Where each jurisdiction's corpus has reached." />
      <p className="cn-note">
        A quarantined source is the licence gate working, not a failure to fix. GLEIPNIR judged the
        licence and refused it; the source is held rather than deleted so the decision stays
        auditable.
      </p>
      <Table
        caption="Corpora by jurisdiction at this site"
        columns={columns}
        rows={corpora.data?.items ?? []}
        rowKey={(row) => row.jurisdiction}
        state={corpora.state}
        problem={corpora.problem}
      />
    </>
  );
}

interface Retention {
  id: string;
  subject: string;
  policy: string;
  dueAt: string;
  approvedBy?: string | null;
  executedAt?: string | null;
  manifestsRetained: boolean;
  daysRemaining: number;
}

/**
 * S06. Corpora approaching the deletion point.
 *
 * Deletion is an approved, ledgered action rather than a timer firing
 * (SAD 7.3), so an overdue action is a decision nobody has taken rather than a
 * job that failed. The screen says which, because those need different people.
 */
export function RetentionSchedule(): JSX.Element {
  const retention = useResource('listRetention', { emptyWhen: pageIsEmpty });
  const [approving, setApproving] = useState<Retention | null>(null);

  const columns = [
    { key: 'subject', header: 'Subject', render: (row: Retention) => row.subject },
    { key: 'policy', header: 'Policy', render: (row: Retention) => row.policy },
    {
      key: 'due',
      header: 'Due',
      render: (row: Retention) => new Date(row.dueAt).toLocaleDateString(),
    },
    {
      key: 'remaining',
      header: 'Remaining',
      numeric: true,
      render: (row: Retention) =>
        row.executedAt != null ? (
          <Badge tone="neutral">executed</Badge>
        ) : row.daysRemaining < 0 ? (
          <Badge tone="danger">{Math.abs(row.daysRemaining)} days overdue</Badge>
        ) : (
          <span>{row.daysRemaining} days</span>
        ),
    },
    {
      key: 'approval',
      header: 'Approval',
      render: (row: Retention) =>
        row.approvedBy == null ? (
          <Badge tone="warning">not approved</Badge>
        ) : (
          <span>{row.approvedBy}</span>
        ),
    },
    {
      key: 'manifests',
      header: 'Manifests',
      render: (row: Retention) =>
        row.manifestsRetained ? (
          <Badge tone="success">retained</Badge>
        ) : (
          <Badge tone="danger">
            not retained
            <span className="jg-sr-only">
              . A lineage that loses its hashes cannot be verified afterwards.
            </span>
          </Badge>
        ),
    },
    {
      key: 'action',
      header: 'Action',
      render: (row: Retention) => (
        <Button
          variant="secondary"
          size="sm"
          state={row.executedAt == null && row.approvedBy == null ? 'ready' : 'readOnly'}
          onClick={() => {
            setApproving(row);
          }}
        >
          Approve deletion
        </Button>
      ),
    },
  ];

  const overdue = retention.data?.overdue ?? 0;

  return (
    <>
      <PageHeading title="Retention" subtitle="Corpora approaching their deletion point." />

      {overdue > 0 ? (
        <p className="cn-resync" role="status" data-testid="retention-overdue">
          {overdue} retention {overdue === 1 ? 'action is' : 'actions are'} past the due date and
          unapproved. Deletion here is an approved, ledgered action rather than a timer firing (SAD
          7.3), so nothing has happened — somebody has to decide.
        </p>
      ) : null}

      <Table
        caption="Retention actions at this site, soonest first"
        columns={columns}
        rows={retention.data?.items ?? []}
        rowKey={(row) => row.id}
        state={retention.state}
        problem={retention.problem}
        stateMessage={
          retention.state === 'empty'
            ? 'No corpus at this site is within its retention window. This is the ordinary state; the 24 month clock starts at curation.'
            : undefined
        }
      />

      {approving === null ? null : (
        <Dialog
          title="Approve this deletion?"
          consequence={
            'The corpus content is deleted and cannot be recovered. The manifests and the ' +
            'ledger entries are retained, so every release built from it stays verifiable ' +
            'and the deletion itself is recorded against your name.'
          }
          confirmLabel="Approve the deletion"
          onConfirm={() => {
            setApproving(null);
          }}
          onDismiss={() => {
            setApproving(null);
          }}
        >
          <p>
            {approving.subject}, under {approving.policy}.
          </p>
        </Dialog>
      )}
    </>
  );
}
