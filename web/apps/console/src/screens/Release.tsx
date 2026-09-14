import type { JSX } from 'react';
import { useState } from 'react';
import {
  Badge,
  Button,
  EvidencePanel,
  LedgerEntryViewer,
  StateSurface,
  Table,
  type EvidenceRow,
} from '@draupnir/jarngreipr';
import { OPERATIONS, urlFor } from '@draupnir/api-client';
import { useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { PageHeading } from './parts';

/**
 * The package, in the order S17 lists it, with the document each download
 * addresses (RF-27). "Download the package" is S17's primary action, and the
 * screen used to print five addresses nothing served. Each document is now
 * generated from the release record and dated by the release, so the same
 * download is the same bytes.
 */
const PACKAGE = [
  {
    document: 'model-card',
    label: 'Model card',
    spoken: 'the model card',
    uri: 'modelCardUri',
    article53: false,
  },
  { document: 'sbom', label: 'SBOM', spoken: 'the SBOM', uri: 'sbomUri', article53: false },
  {
    document: 'lineage',
    label: 'Lineage attestation',
    spoken: 'the lineage attestation',
    uri: 'lineageUri',
    article53: false,
  },
  {
    document: 'training-summary',
    label: 'Training data summary',
    spoken: 'the training data summary',
    uri: 'trainingSummaryUri',
    article53: true,
  },
  {
    document: 'copyright-policy',
    label: 'Copyright policy',
    spoken: 'the copyright policy',
    uri: 'copyrightPolicyUri',
    article53: true,
  },
] as const;

/**
 * S14 Model detail, S17 Release package, S27 Ledger entry detail and
 * S28 Attestation export.
 *
 * The four screens an auditor walks through after the lineage explorer, in the
 * order the questions arrive: what is this model, what was published with it,
 * what does the ledger say about it, and can I take that away and check it
 * somewhere else.
 */

interface Artefact {
  sha256: string;
  uri: string;
  kind: string;
  size: number;
  locality?: string[];
  immutableAt?: string | null;
}

/**
 * S14. A model's artefacts and its evidence.
 *
 * Every artefact the producing run made, not only the one that was asked for.
 * A quantised build and the adapter it came from are the same model in two
 * forms, and an auditor comparing them should not have to find the second by
 * guessing its digest.
 */
export function ModelDetail({ artefact }: { artefact: string }): JSX.Element {
  const model = useResource('getModel', { params: { artefact } });
  const data = model.data;

  const columns = [
    { key: 'kind', header: 'Kind', render: (row: Artefact) => row.kind },
    {
      key: 'uri',
      header: 'URI',
      render: (row: Artefact) => <code className="cn-digest">{row.uri}</code>,
    },
    {
      key: 'sha256',
      header: 'Digest',
      render: (row: Artefact) => (
        <a {...linkProps(`/models/${row.sha256}/lineage`)} className="cn-digest">
          {row.sha256.slice(0, 16)}…
        </a>
      ),
    },
    {
      key: 'size',
      header: 'Size',
      numeric: true,
      render: (row: Artefact) => `${(row.size / 1_000_000).toFixed(1)} MB`,
    },
    {
      key: 'locality',
      header: 'Held at',
      render: (row: Artefact) => (row.locality ?? []).join(', ') || '—',
    },
    {
      key: 'immutable',
      header: 'Sealed',
      render: (row: Artefact) =>
        row.immutableAt == null ? (
          <Badge tone="warning">still mutable</Badge>
        ) : (
          <Badge tone="success">{new Date(row.immutableAt).toLocaleDateString()}</Badge>
        ),
    },
  ];

  const evidence: EvidenceRow[] = (data?.gates ?? []).map((gate) => ({
    gate: gate.gate,
    statement: `at or above ${String(gate.baselineValue ?? 0)}`,
    passed: gate.passed,
    measurement: {
      value: gate.value,
      baseline: gate.baselineValue ?? 0,
      margin: gate.margin ?? 0,
    },
  }));

  return (
    <>
      <PageHeading title={data?.name ?? 'Model'} subtitle={artefact} />

      <StateSurface state={model.state} problem={model.problem} label="Model" reserve="xl">
        {data === undefined ? null : (
          <>
            <dl className="jg-facts">
              <dt>Jurisdiction</dt>
              <dd>{data.jurisdiction ?? 'not recorded'}</dd>
              <dt>Run</dt>
              <dd>
                {data.runId == null ? (
                  'no run recorded'
                ) : (
                  <a {...linkProps(`/runs/${data.runId}`)}>{data.runId}</a>
                )}
              </dd>
              <dt>State</dt>
              <dd>{data.state ?? 'unknown'}</dd>
              <dt>Specification</dt>
              <dd data-jg-mono="true">{data.specHash ?? 'not recorded'}</dd>
              <dt>Released</dt>
              <dd>
                {data.released ? (
                  <a {...linkProps(`/models/${artefact}/release`)}>open the release package</a>
                ) : (
                  'not released'
                )}
              </dd>
            </dl>

            <section aria-labelledby="cn-artefacts-heading">
              <h2 id="cn-artefacts-heading">Artefacts</h2>
              <Table
                caption="Every artefact this run produced"
                columns={columns}
                rows={data.artefacts}
                rowKey={(row) => row.sha256}
                state="ready"
              />
            </section>

            <section aria-labelledby="cn-evidence-heading">
              <h2 id="cn-evidence-heading">Gate results</h2>
              {evidence.length === 0 ? (
                <p className="cn-note">
                  This model has no gate results. It has not been evaluated, which is a state rather
                  than a failure.
                </p>
              ) : (
                <EvidencePanel
                  suiteVersion={data.gates?.[0]?.suiteVersion ?? 'unknown'}
                  rows={evidence}
                />
              )}
            </section>

            <div className="cn-card__actions">
              <a className="cn-action" {...linkProps(`/models/${artefact}/lineage`)}>
                Walk the lineage
              </a>
            </div>
          </>
        )}
      </StateSurface>
    </>
  );
}

/**
 * S17. The release package.
 *
 * The two Article 53 artefacts are on the face of it beside the card and the
 * SBOM, not in a subsection. SAD 9A and Decision S11 make them generated
 * artefacts of the release rather than documents somebody writes afterwards,
 * and a package screen that filed them separately would suggest otherwise.
 */
export function ReleasePackage({ artefact }: { artefact: string }): JSX.Element {
  const release = useResource('getRelease', { params: { artefact } });
  const data = release.data;

  return (
    <>
      <PageHeading title={data?.model ?? 'Release'} subtitle={artefact} />

      <StateSurface
        state={release.state}
        problem={release.problem}
        label="Release package"
        reserve="xl"
        stateMessage={
          release.state === 'error'
            ? 'This artefact has no release record. An artefact that exists and is unreleased is not an error.'
            : undefined
        }
      >
        {data === undefined ? null : (
          <>
            {data.soleApproverException ? (
              <p className="cn-sole" role="note" data-testid="release-sole-approver">
                <strong>This release was approved by a single approver.</strong> The exception is
                recorded in the ledger and appears on the model card. Nothing is wrong: it is a
                disclosed fact about how the release was signed (SAD 9.4).
              </p>
            ) : null}

            <dl className="jg-facts">
              <dt>Approved by</dt>
              <dd>{data.approver}</dd>
              <dt>Published</dt>
              <dd>
                {data.publishedAt == null
                  ? 'not published'
                  : new Date(data.publishedAt).toLocaleString()}
              </dd>
              <dt>Anchored</dt>
              <dd>
                {data.anchoredAt == null ? (
                  <Badge tone="warning">
                    not countersigned
                    <span className="jg-sr-only">
                      . The chain head has not been anchored in the federation.
                    </span>
                  </Badge>
                ) : (
                  <Badge tone="success">{new Date(data.anchoredAt).toLocaleString()}</Badge>
                )}
              </dd>
              <dt>Signature</dt>
              <dd data-jg-mono="true">{data.signature}</dd>
            </dl>

            <section aria-labelledby="cn-package-heading">
              <h2 id="cn-package-heading">Package contents</h2>
              <ul className="cn-package" data-testid="release-contents">
                {PACKAGE.map((item) => (
                  <li key={item.document}>
                    <span className="cn-package__label">
                      {item.label}
                      {item.article53 ? (
                        <span className="cn-package__note">EU AI Act Article 53</span>
                      ) : null}
                    </span>
                    <code>{data[item.uri]}</code>
                    <a
                      className="jg-button"
                      data-jg-variant="secondary"
                      data-jg-size="sm"
                      href={urlFor(OPERATIONS.downloadReleaseDocument, {
                        params: { artefact, document: item.document },
                      })}
                      download
                      data-testid={`download-${item.document}`}
                    >
                      Download<span className="jg-sr-only"> {item.spoken}</span>
                    </a>
                  </li>
                ))}
              </ul>
              <p className="cn-note">
                The Article 53 artefacts are generated from the release, not authored beside it
                (Decision S11). A summary somebody typed would be a summary that stops matching the
                corpus.
              </p>
            </section>

            <div className="cn-card__actions">
              <a className="cn-action" {...linkProps(`/models/${artefact}/attestation`)}>
                Export the attestation
              </a>
            </div>
          </>
        )}
      </StateSurface>
    </>
  );
}

/**
 * S28. The attestation export.
 *
 * An incomplete chain exports **unsigned**, and the screen says so before
 * offering the download. Signing an attestation over a gap would certify the
 * gap: a signature is read as a statement that somebody checked, and nobody
 * checked what is missing.
 */
export function AttestationExport({ artefact }: { artefact: string }): JSX.Element {
  const attestation = useResource('exportAttestation', { params: { artefact } });
  const data = attestation.data;
  const [copied, setCopied] = useState(false);

  return (
    <>
      <PageHeading title="Attestation" subtitle={artefact} />

      <StateSurface
        state={attestation.state}
        problem={attestation.problem}
        label="Attestation"
        reserve="xl"
      >
        {data === undefined ? null : (
          <>
            <p
              className={data.complete ? 'cn-lineage__banner' : 'cn-lineage__banner'}
              data-jg-complete={String(data.complete)}
              data-testid="attestation-completeness"
              role="status"
            >
              {data.complete
                ? 'The chain is complete and this bundle is signed.'
                : `This chain has ${String((data.gaps ?? []).length)} gap${(data.gaps ?? []).length === 1 ? '' : 's'}, so the bundle is exported unsigned. Signing an attestation over a gap would certify the gap.`}
            </p>

            <dl className="jg-facts">
              <dt>Issued</dt>
              <dd>{new Date(data.issuedAt).toLocaleString()}</dd>
              <dt>Issuing site</dt>
              <dd>{data.siteId}</dd>
              <dt>Bundle digest</dt>
              <dd data-jg-mono="true" data-testid="attestation-digest">
                {data.payloadSha256}
              </dd>
              <dt>Signature</dt>
              <dd data-jg-mono="true">{data.signature ?? 'unsigned — the chain is incomplete'}</dd>
            </dl>

            <Button
              onClick={() => {
                void navigator.clipboard
                  .writeText(JSON.stringify(data.payload, null, 2))
                  .then(() => {
                    setCopied(true);
                  })
                  .catch(() => {
                    setCopied(false);
                  });
              }}
            >
              Copy the bundle
            </Button>
            <span role="status" className="cn-error__copied">
              {copied ? 'Copied. Verify it against the digest above.' : ''}
            </span>

            {/* Focusable because it scrolls: a keyboard user has to be able
                to reach and scroll a long bundle (WCAG 2.1.1). */}
            {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
            <pre className="cn-ledger__payload" tabIndex={0} aria-label="The canonical bundle">
              {JSON.stringify(data.payload, null, 2)}
            </pre>
          </>
        )}
      </StateSurface>
    </>
  );
}

/**
 * S27. One ledger entry.
 *
 * The hash is recomputed by the API from `prev_hash` and the canonical payload
 * and returned beside the stored one. An entry viewer that rendered only the
 * stored hash would prove nothing: the stored hash is exactly what a tamperer
 * would have rewritten.
 */
export function LedgerEntryDetail({ entryHash }: { entryHash: string }): JSX.Element {
  const entry = useResource('getLedgerEntry', { params: { entry_hash: entryHash } });
  const data = entry.data;

  return (
    <>
      <PageHeading
        title={`Ledger entry ${data ? `#${String(data.seq)}` : ''}`}
        subtitle={entryHash}
      />

      <StateSurface state={entry.state} problem={entry.problem} label="Ledger entry" reserve="xl">
        {data === undefined ? null : (
          <>
            <p className="cn-chain" data-jg-verified={String(data.verified)} role="status">
              {data.verified
                ? 'Recomputed here from the previous hash and the canonical payload; it matches the recorded hash.'
                : `Recomputed hash ${data.recomputedHash} does not match the recorded ${data.entryHash}. This entry has been altered since it was written.`}
            </p>

            <LedgerEntryViewer
              entry={{
                sequence: data.seq,
                kind: data.transition,
                recordedAt: new Date(data.ts).toLocaleString(),
                actor: data.actor,
                digest: data.entryHash,
                previousDigest: data.prevHash,
                payload: JSON.stringify(data.payload, null, 2),
                verified: data.verified,
              }}
            />

            <dl className="jg-facts">
              <dt>Subject</dt>
              <dd>
                {data.subjectType} <code>{data.subjectId}</code>
              </dd>
              <dt>Site</dt>
              <dd>{data.siteId}</dd>
            </dl>

            <div className="cn-card__actions">
              <a className="cn-action" {...linkProps('/audit')}>
                Back to the ledger
              </a>
            </div>
          </>
        )}
      </StateSurface>
    </>
  );
}
