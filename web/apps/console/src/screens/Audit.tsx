import type { JSX } from 'react';
import { useState } from 'react';
import {
  Badge,
  Button,
  Dialog,
  LineageTree,
  StateSurface,
  Table,
  type LineageNode,
} from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey } from '@draupnir/api-client';
import type { Model } from '@draupnir/api-client';
import { pageIsEmpty, problemOf, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { ErrorSurface, PageHeading } from './parts';

/**
 * S13 Model registry, S16 Lineage explorer, S20 Publish, S26 Ledger. Journey J4.
 *
 * "A complete lineage is reached in three interactions or fewer": Models,
 * select a release, lineage. Three. The registry therefore carries the artefact
 * digest, which is the lineage key, so selecting a release is one click rather
 * than a click and a lookup.
 */

export function ModelRegistry(): JSX.Element {
  const models = useResource('listModels', { query: { limit: 50 }, emptyWhen: pageIsEmpty });

  const columns = [
    {
      key: 'name',
      header: 'Model',
      render: (row: Model) => (
        <a {...linkProps(`/models/${row.artefact}`)} data-testid={`model-link-${row.name}`}>
          {row.name}
        </a>
      ),
    },
    {
      key: 'jurisdiction',
      header: 'Jurisdiction',
      render: (row: Model) => row.jurisdiction ?? '—',
    },
    { key: 'kind', header: 'Kind', render: (row: Model) => row.kind },
    {
      key: 'released',
      header: 'Release',
      render: (row: Model) =>
        row.released ? (
          <Badge tone={row.anchored ? 'success' : 'warning'}>
            {row.anchored ? 'released and anchored' : 'released, not anchored'}
          </Badge>
        ) : (
          <Badge tone="neutral">not released</Badge>
        ),
    },
    {
      key: 'sole',
      header: 'Approval',
      render: (row: Model) =>
        row.soleApproverException ? (
          <Badge tone="warning">
            <span className="jg-sr-only">Disclosed: </span>sole approver
          </Badge>
        ) : (
          <span>{row.approver ?? '—'}</span>
        ),
    },
  ];

  return (
    <>
      <PageHeading title="Models" />
      <Table
        caption="The model registry at this site"
        columns={columns}
        rows={models.data?.items ?? []}
        rowKey={(row) => row.artefact}
        state={models.state}
        problem={models.problem}
      />
    </>
  );
}

/**
 * S16 Lineage explorer.
 *
 * A gap renders as a marked node stating what is missing, never as a shorter
 * tree. A chain that simply stops looks complete to anyone who does not
 * already know how long it should be, which is everyone reading it for the
 * first time — and an auditor reading it for the first time is the case this
 * screen is for.
 */
export function LineageExplorer({ artefact }: { artefact: string }): JSX.Element {
  const lineage = useResource('getLineage', { params: { artefact } });
  const data = lineage.data;
  // Optional in the contract, so defaulted once here rather than at six
  // call sites. An absent list is not a gap; a gap is a listed gap.
  const gaps = data?.gaps ?? [];
  const licences = data?.licences ?? [];

  const roots: LineageNode[] =
    data === undefined
      ? []
      : [
          {
            id: 'root',
            kind: 'release',
            label: data.artefact.slice(0, 16),
            digest: data.artefact,
            children: (data.nodes ?? []).map((node, index) => nodeOf(node, index)),
          },
        ];

  return (
    <>
      <PageHeading title="Lineage" subtitle={artefact} />

      <StateSurface
        state={lineage.state}
        problem={lineage.problem}
        label="Lineage"
        minHeight="20rem"
      >
        {data === undefined ? null : (
          <>
            <p
              className="cn-lineage__banner"
              data-testid="lineage-completeness"
              role="status"
              data-jg-complete={String(data.complete)}
            >
              {data.complete
                ? 'This chain reaches licensed roots with no gaps.'
                : `This chain has ${String(gaps.length)} gap${gaps.length === 1 ? '' : 's'}. Each is marked in the tree below; none is omitted.`}
            </p>

            {gaps.length === 0 ? null : (
              <ul className="cn-lineage__gaps" data-testid="lineage-gaps">
                {gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            )}

            <LineageTree label={`Lineage of ${artefact.slice(0, 12)}`} roots={roots} />

            <section aria-labelledby="cn-licences-heading">
              <h2 id="cn-licences-heading">Licences in this chain</h2>
              {licences.length === 0 ? (
                <p>No licence is recorded in this chain. That is a gap, not an absence.</p>
              ) : (
                <ul data-testid="lineage-licences">
                  {licences.map((licence) => (
                    <li key={licence}>{licence}</li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="cn-corpus-heading">
              <h2 id="cn-corpus-heading">Corpus hashes</h2>
              <ul className="cn-hashes" data-testid="lineage-corpus-hashes">
                {(data.corpusHashes ?? []).map((hash: string) => (
                  <li key={hash}>
                    <code>{hash}</code>
                  </li>
                ))}
              </ul>
            </section>

            <PublishPanel artefact={artefact} />
          </>
        )}
      </StateSurface>
    </>
  );
}

function nodeOf(node: Record<string, unknown>, index: number): LineageNode {
  const gap = typeof node.gap === 'string' ? node.gap : null;
  const label = typeof node.label === 'string' ? node.label : 'unnamed';
  const kind = typeof node.kind === 'string' ? node.kind : 'node';
  return {
    id: `${kind}-${String(index)}`,
    kind,
    // A gap is a node that says what is missing. Rendering it with the label
    // alone would make it look like an ordinary link in the chain.
    label: gap === null ? label : `${label} — MISSING: ${gap}`,
    digest: typeof node.digest === 'string' ? node.digest : undefined,
  };
}

/**
 * S20 Publish, and its partitioned variant. AC-U12.
 *
 * "A partitioned site is stated plainly in the interface, and the release
 * action is disabled with the reason given, rather than failing with a generic
 * error." So the control is disabled and the panel above it says why. Leaving
 * it enabled to fail would tell the approver the action was available, let them
 * take it, and then take it back.
 */
function PublishPanel({ artefact }: { artefact: string }): JSX.Element {
  const sites = useResource('listSites', {});
  const health = useResource('getHealth', {});
  const here = sites.data?.items.find((site) => site.id === health.data?.siteId);
  const partitioned = here?.anchorState === 'PARTITIONED';
  // Named separately: `partitioned` does not narrow `here` for TypeScript,
  // and reaching for `here!` to work around that is how a null slips in.
  const siteName = here?.name ?? 'This site';

  const [confirm, setConfirm] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemOf> | null>(null);

  async function publish(): Promise<void> {
    setConfirm(false);
    try {
      await call('publishRelease', {
        params: { artefact },
        body: {},
        idempotencyKey: idempotencyKey(),
      });
      setOutcome('Published. The release is in the registry and the ledger.');
    } catch (cause) {
      setProblem(
        cause instanceof ApiError
          ? problemOf(cause)
          : { title: 'The publication did not complete', detail: String(cause) },
      );
    }
  }

  return (
    <section className="cn-publish" aria-labelledby="cn-publish-heading">
      <h2 id="cn-publish-heading">Publish</h2>

      {partitioned ? (
        <p className="cn-publish__blocked" role="status" data-testid="publish-blocked">
          <strong>{siteName} is partitioned from the federation.</strong> Release requires the chain
          head to be countersigned by another site, and no site can be reached. Training and
          evaluation continue unaffected (Decision S8). The control below is disabled for this
          reason rather than left available to fail.
        </p>
      ) : null}

      <Button
        state={partitioned ? 'partitioned' : 'ready'}
        stateMessage={
          partitioned
            ? 'Unavailable: this site is partitioned from the federation and the chain head cannot be countersigned.'
            : undefined
        }
        onClick={() => {
          setConfirm(true);
        }}
      >
        Publish to registry
      </Button>

      {outcome === null ? null : <p role="status">{outcome}</p>}
      {problem === null ? null : <ErrorSurface problem={problem} />}

      {confirm ? (
        <Dialog
          title="Publish this release?"
          consequence={
            'The artefact, its model card, its SBOM and its Article 53 artefacts are published ' +
            'to the registry and the release is anchored in the federation. A published release ' +
            'is withdrawn by a separate recorded action, not by undoing this one.'
          }
          confirmLabel="Publish"
          onConfirm={() => {
            void publish();
          }}
          onDismiss={() => {
            setConfirm(false);
          }}
        >
          <p>
            Artefact <code>{artefact.slice(0, 16)}…</code>
          </p>
        </Dialog>
      ) : null}
    </section>
  );
}

/** S26 Ledger explorer, with the chain verification travelling with the slice. */
export function LedgerExplorer(): JSX.Element {
  const ledger = useResource('getLedger', { query: { limit: 100 }, emptyWhen: pageIsEmpty });
  const data = ledger.data;

  const columns = [
    { key: 'seq', header: 'Seq', numeric: true, render: (row: LedgerRow) => row.seq },
    { key: 'ts', header: 'When', render: (row: LedgerRow) => new Date(row.ts).toLocaleString() },
    { key: 'actor', header: 'Actor', render: (row: LedgerRow) => row.actor },
    { key: 'subject', header: 'Subject', render: (row: LedgerRow) => row.subjectType },
    { key: 'transition', header: 'Transition', render: (row: LedgerRow) => row.transition },
    {
      key: 'hash',
      header: 'Entry hash',
      render: (row: LedgerRow) => (
        <a
          {...linkProps(`/audit/${row.entryHash}`)}
          className="cn-digest"
          data-testid={`ledger-entry-${String(row.seq)}`}
        >
          {row.entryHash.slice(0, 12)}…
        </a>
      ),
    },
  ];

  return (
    <>
      <PageHeading title="Audit" subtitle="This site's ledger segment" />

      {data === undefined ? null : (
        <p
          className="cn-chain"
          role="status"
          data-testid="chain-verification"
          data-jg-verified={String(data.verified)}
        >
          {data.verified
            ? 'The returned slice chains end to end. Every entry links to the one before it.'
            : `The chain diverges: ${data.divergence ?? 'the slice does not link'}. This is shown rather than hidden, because an endpoint that returned entries without saying whether they chain makes tampering look like data.`}
        </p>
      )}

      <Table
        caption="Ledger entries at this site, newest first"
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.entryHash}
        state={ledger.state}
        problem={ledger.problem}
      />
    </>
  );
}

interface LedgerRow {
  seq: number;
  ts: string;
  actor: string;
  subjectType: string;
  transition: string;
  entryHash: string;
}
