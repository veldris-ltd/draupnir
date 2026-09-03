import type { JSX } from 'react';
import { useState } from 'react';
import { Badge, Button, Checkbox, Input, Select, Table } from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey } from '@draupnir/api-client';
import type { Source } from '@draupnir/api-client';
import { pageIsEmpty, problemOf, useResource } from '../api/useResource';
import { linkProps, navigate } from '../routing';
import { ErrorSurface, PageHeading } from './parts';

/**
 * S02 Corpus list, S03 Source register, S04 Register source wizard. Journey J1.
 *
 * The data protection gate is step two of four, and it is where the wizard
 * earns its shape: when personal data is declared the DPIA reference becomes
 * required and a panel explains why. Legal corpora are dense with named
 * individuals, so the gate applies to most jurisdictions rather than a
 * minority, and an operator who meets it once should understand that it is the
 * ordinary case rather than an obstruction.
 */

export function SourceRegister(): JSX.Element {
  const sources = useResource('listSources', { query: { limit: 50 }, emptyWhen: pageIsEmpty });

  const columns = [
    { key: 'url', header: 'Source', render: (row: Source) => row.url },
    { key: 'jurisdiction', header: 'Jurisdiction', render: (row: Source) => row.jurisdiction },
    { key: 'licence', header: 'Licence', render: (row: Source) => row.licenceSpdx },
    {
      key: 'personal',
      header: 'Personal data',
      render: (row: Source) =>
        row.personalData ? (
          <Badge tone={row.dpiaRef == null ? 'danger' : 'warning'}>
            {row.dpiaRef == null ? 'declared, no DPIA' : `DPIA ${row.dpiaRef}`}
          </Badge>
        ) : (
          <Badge tone="neutral">none declared</Badge>
        ),
    },
    {
      key: 'state',
      header: 'State',
      render: (row: Source) => (
        <Badge tone={row.state === 'QUARANTINED' ? 'danger' : 'info'}>{row.state}</Badge>
      ),
    },
    {
      key: 'sha256',
      header: 'Digest',
      render: (row: Source) => <code className="cn-digest">{row.sha256.slice(0, 12)}…</code>,
    },
  ];

  return (
    <>
      <PageHeading
        title="Corpora"
        action={
          <a className="cn-action" {...linkProps('/corpora/register')}>
            Register a source
          </a>
        }
      />
      <Table
        caption="The licence register at this site"
        columns={columns}
        rows={sources.data?.items ?? []}
        rowKey={(row) => row.id}
        state={sources.state}
        problem={sources.problem}
      />
    </>
  );
}

const STEPS = ['Source', 'Data protection', 'Residency', 'Review'] as const;

export function RegisterSource(): JSX.Element {
  const [step, setStep] = useState(0);
  const [url, setUrl] = useState('');
  const [jurisdiction, setJurisdiction] = useState('GBR');
  const [licence, setLicence] = useState('OGL-UK-3.0');
  const [digest, setDigest] = useState('');
  const [attribution, setAttribution] = useState(true);
  const [personalData, setPersonalData] = useState(false);
  const [dpia, setDpia] = useState('');
  const [residency, setResidency] = useState('');
  const [problem, setProblem] = useState<ReturnType<typeof problemOf> | null>(null);
  const [registered, setRegistered] = useState<string | null>(null);

  const dpiaMissing = personalData && dpia.trim() === '';
  // The register records the digest of the file the curator retrieved, and
  // `retrievedAt` is required alongside it: the source has been fetched, so
  // its digest exists. Ingest hashes the *corpus* that is built from it,
  // which is a later and different fact.
  const digestValid = /^[0-9a-f]{64}$/.test(digest.trim());

  async function register(): Promise<void> {
    try {
      const result = await call('registerSource', {
        body: {
          jurisdiction,
          url,
          licenceSpdx: licence,
          attributionRequired: attribution,
          retrievedAt: new Date().toISOString(),
          sha256: digest.trim(),
          personalData,
          dpiaRef: personalData ? dpia.trim() : null,
          residencyConstraint:
            residency === '' ? [] : residency.split(',').map((part) => part.trim()),
        },
        idempotencyKey: idempotencyKey(),
      });
      const body = result.data as { id: string };
      setRegistered(body.id);
    } catch (cause) {
      setProblem(
        cause instanceof ApiError
          ? problemOf(cause)
          : { title: 'The source could not be registered', detail: String(cause) },
      );
    }
  }

  return (
    <>
      <PageHeading title="Register a source" subtitle={`Step ${String(step + 1)} of 4`} />

      <ol className="cn-steps" aria-label="Registration steps">
        {STEPS.map((label, index) => (
          <li key={label} aria-current={index === step ? 'step' : undefined}>
            {label}
          </li>
        ))}
      </ol>

      {step === 0 ? (
        <div className="cn-form">
          <Input
            label="Source URL"
            value={url}
            hint="Where the corpus is retrieved from. Recorded in the licence register."
            onChange={setUrl}
          />
          <Select
            label="Jurisdiction"
            value={jurisdiction}
            options={[
              { value: 'GBR', label: 'GBR — United Kingdom' },
              { value: 'IRL', label: 'IRL — Ireland' },
              { value: 'DEU', label: 'DEU — Germany' },
              { value: 'FRA', label: 'FRA — France' },
            ]}
            onChange={setJurisdiction}
          />
          <Input
            label="Licence (SPDX)"
            value={licence}
            hint="Judged against the policy bundle. A licence that fails quarantines the source."
            onChange={setLicence}
          />
          <Input
            label="Content digest (SHA-256)"
            value={digest}
            required
            hint="Of the file you retrieved. 64 hexadecimal characters."
            error={
              digest !== '' && !digestValid
                ? 'A SHA-256 digest is 64 hexadecimal characters.'
                : undefined
            }
            onChange={setDigest}
          />
          <Checkbox
            label="Attribution is required by this licence"
            checked={attribution}
            onChange={setAttribution}
          />
        </div>
      ) : null}

      {step === 1 ? (
        <div className="cn-form">
          <Checkbox
            label="This source contains personal data"
            checked={personalData}
            onChange={setPersonalData}
          />
          {personalData ? (
            <section className="cn-dpia" aria-labelledby="cn-dpia-heading" data-testid="dpia-panel">
              <h2 id="cn-dpia-heading">A DPIA reference is required</h2>
              <p>
                Legal corpora are dense with named individuals — parties, judges, witnesses — so
                this gate applies to most jurisdictions rather than a minority. The reference is
                recorded against the source and appears in the lineage.
              </p>
              <Input
                label="DPIA reference"
                value={dpia}
                required
                error={
                  dpiaMissing
                    ? 'A DPIA reference is required when personal data is declared.'
                    : undefined
                }
                onChange={setDpia}
              />
            </section>
          ) : null}
        </div>
      ) : null}

      {step === 2 ? (
        <div className="cn-form">
          <Input
            label="Residency constraint"
            value={residency}
            hint="Comma separated site identifiers permitted to hold this corpus. Empty means no constraint."
            onChange={setResidency}
          />
        </div>
      ) : null}

      {step === 3 ? (
        <dl className="jg-facts" data-testid="register-review">
          <dt>Source</dt>
          <dd>{url}</dd>
          <dt>Jurisdiction</dt>
          <dd>{jurisdiction}</dd>
          <dt>Licence</dt>
          <dd>{licence}</dd>
          <dt>Content digest</dt>
          <dd data-jg-mono="true">{digest}</dd>
          <dt>Personal data</dt>
          <dd>{personalData ? `declared, DPIA ${dpia}` : 'none declared'}</dd>
          <dt>Residency</dt>
          <dd>{residency === '' ? 'unconstrained' : residency}</dd>
        </dl>
      ) : null}

      <div className="cn-form__actions">
        <Button
          variant="secondary"
          state={step === 0 ? 'readOnly' : 'ready'}
          onClick={() => {
            setStep((value) => Math.max(0, value - 1));
          }}
        >
          Back
        </Button>
        {step < 3 ? (
          <Button
            state={
              (step === 0 && !digestValid) || (step === 1 && dpiaMissing) ? 'readOnly' : 'ready'
            }
            onClick={() => {
              setStep((value) => Math.min(3, value + 1));
            }}
          >
            Continue
          </Button>
        ) : (
          <Button
            onClick={() => {
              void register();
            }}
          >
            Register and ingest
          </Button>
        )}
      </div>

      {problem === null ? null : <ErrorSurface problem={problem} />}

      {registered === null ? null : (
        <section className="cn-submitted" role="status" aria-labelledby="cn-registered-heading">
          <h2 id="cn-registered-heading">Source registered</h2>
          <p data-testid="registered-id">
            Registered as <code>{registered}</code>. Ingest hashes the content and the licence gate
            judges it.
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              navigate('/corpora');
            }}
          >
            Back to the register
          </Button>
        </section>
      )}
    </>
  );
}
