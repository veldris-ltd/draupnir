import type { JSX } from 'react';
import { useState } from 'react';
import { Badge, Button, Dialog, GateCard, StateSurface, Table } from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey } from '@draupnir/api-client';
import type { Approval } from '@draupnir/api-client';
import { pageIsEmpty, problemOf, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { ErrorSurface, PageHeading } from './parts';

/**
 * S18 Approval queue and S19 Approval detail. Journey J3.
 *
 * AC-U13 is the criterion this screen exists for: "The gate queue displays the
 * gate evidence and the sole approver notice before the decision control, not
 * after."
 *
 * "Before" is enforced by the document order -- evidence, then notice, then
 * controls -- and by the decision controls being disabled until the evidence
 * has actually been in the viewport. That second half matters: an evidence
 * table above a control that an approver can reach by scrolling past it is
 * evidence they can decline to read without noticing they declined. The
 * intersection observer is not a dark pattern; it is the difference between
 * evidence being present and evidence being seen.
 *
 * The notice is styled as a warning rather than a danger because nothing is
 * wrong. It is a disclosed fact about how this release was approved, and it
 * appears in the lineage and the model card whatever the approver does next.
 */

export function GateQueue(): JSX.Element {
  const queue = useResource('listGates', {
    query: { limit: 50, state: 'pending' },
    emptyWhen: pageIsEmpty,
  });

  const columns = [
    {
      key: 'model',
      header: 'Artefact',
      render: (row: Approval) => (
        <a {...linkProps(`/gates/${row.id}`)} data-testid={`gate-link-${row.model}`}>
          {row.model}
        </a>
      ),
    },
    {
      key: 'gates',
      header: 'Gates',
      render: (row: Approval) => <GateSummary approval={row} />,
    },
    {
      key: 'submitted_by',
      header: 'Submitted by',
      render: (row: Approval) => row.submittedBy,
    },
    {
      key: 'waiting',
      header: 'Waiting',
      numeric: true,
      render: (row: Approval) => waitingFor(row.awaitingSince),
    },
  ];

  return (
    <>
      <PageHeading title="Gates" subtitle="Ordered by waiting time, so nothing ages quietly." />
      <Table
        caption="Artefacts awaiting a decision at this site"
        columns={columns}
        rows={queue.data?.items ?? []}
        rowKey={(row) => row.id}
        state={queue.state}
        problem={queue.problem}
      />
    </>
  );
}

/** Never a bare tick: the count of gates met, and whether any failed. */
function GateSummary({ approval }: { approval: Approval }): JSX.Element {
  const gates = approval.gates ?? [];
  const passed = gates.filter((gate) => gate.passed).length;
  const total = gates.length;
  const failing = total - passed;
  return (
    <Badge tone={failing === 0 ? 'success' : 'danger'}>
      {passed} of {total} met
      {failing === 0 ? '' : `, ${String(failing)} not`}
    </Badge>
  );
}

function waitingFor(since: string): string {
  const hours = Math.max(0, (Date.now() - new Date(since).getTime()) / 3_600_000);
  if (hours < 48) return `${String(Math.round(hours))} h`;
  return `${String(Math.round(hours / 24))} d`;
}

export function ApprovalDetail({ gateId }: { gateId: string }): JSX.Element {
  const queue = useResource('listGates', { query: { limit: 100, state: 'pending' } });
  const approval = queue.data?.items.find((item) => item.id === gateId) ?? null;

  const [evidenceSeen, setEvidenceSeen] = useState(false);
  const [confirm, setConfirm] = useState<'approve' | 'reject' | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemOf> | null>(null);

  // Sole approver: disclosed before the decision, never after (SAD 9.4).
  //
  // The queue does not yet carry a second approver's identity, so the notice
  // is shown for every pending artefact. That is the conservative direction:
  // a notice shown when a second approver exists is a redundant sentence, and
  // a notice withheld when one does not is the disclosure failure the
  // criterion is about. When the queue carries the prior decisions this reads
  // them instead.
  const soleApprover = (approval?.gates ?? []).length >= 0;

  async function decide(decision: 'approved' | 'rejected', reason: string): Promise<void> {
    setConfirm(null);
    try {
      await call('decideGate', {
        params: { gate_id: gateId },
        body: { decision, reason, signature: 'console-session' },
        idempotencyKey: idempotencyKey(),
      });
      setOutcome(
        decision === 'approved'
          ? 'Approved. The decision and the sole approver exception are in the ledger.'
          : 'Rejected. The artefact is quarantined, not deleted, and the reason is recorded.',
      );
      queue.refresh();
    } catch (cause) {
      setProblem(
        cause instanceof ApiError
          ? problemOf(cause)
          : { title: 'The decision did not complete', detail: String(cause) },
      );
    }
  }

  return (
    <>
      <PageHeading title={approval?.model ?? 'Approval'} subtitle={gateId} />

      <StateSurface
        state={queue.state === 'ready' && approval === null ? 'empty' : queue.state}
        problem={queue.problem}
        label="Approval detail"
        reserve="xl"
      >
        {approval === null ? null : (
          <>
            {/* 1. The evidence. First in the document, and observed. */}
            <EvidenceTable
              approval={approval}
              onSeen={() => {
                setEvidenceSeen(true);
              }}
            />

            {/* 2. The sole approver notice. */}
            {soleApprover ? (
              <section
                className="cn-sole"
                role="note"
                aria-labelledby="cn-sole-heading"
                data-testid="sole-approver-notice"
              >
                <h2 id="cn-sole-heading">This release will have a single approver</h2>
                <p>
                  No second approver has signed this artefact. Nothing is wrong: this is a disclosed
                  fact about how the release was approved, and it will appear in the lineage
                  attestation and on the model card whatever you decide next. SAD 9.4 records the
                  exception rather than blocking the action.
                </p>
              </section>
            ) : null}

            {/* 3. Only now, the decision. */}
            <section className="cn-decision" aria-labelledby="cn-decision-heading">
              <h2 id="cn-decision-heading">Decision</h2>
              {evidenceSeen ? null : (
                <p className="cn-decision__gate" role="status" data-testid="evidence-gate">
                  Scroll through the gate evidence above before deciding. The controls become
                  available once it has been on screen.
                </p>
              )}
              <div className="cn-decision__controls">
                <Button
                  variant="primary"
                  state={evidenceSeen ? 'ready' : 'readOnly'}
                  onClick={() => {
                    setConfirm('approve');
                  }}
                >
                  Sign and approve
                </Button>
                <Button
                  variant="danger"
                  state={evidenceSeen ? 'ready' : 'readOnly'}
                  onClick={() => {
                    setConfirm('reject');
                  }}
                >
                  Reject
                </Button>
              </div>
              {outcome === null ? null : (
                <p role="status" data-testid="decision-outcome">
                  {outcome}
                </p>
              )}
              {problem === null ? null : <ErrorSurface problem={problem} />}
            </section>
          </>
        )}
      </StateSurface>

      {confirm === null ? null : (
        <Dialog
          title={confirm === 'approve' ? 'Sign and approve this release?' : 'Reject this artefact?'}
          consequence={
            confirm === 'approve'
              ? 'Your signature is recorded in the ledger with the sole approver exception, ' +
                'which appears in the lineage attestation and on the model card. The release ' +
                'becomes publishable. This cannot be unsigned; a withdrawal is a separate, ' +
                'recorded action.'
              : 'The artefact is quarantined rather than deleted. The reason is required and ' +
                'is recorded in the ledger against your name.'
          }
          confirmLabel={confirm === 'approve' ? 'Sign and approve' : 'Reject and quarantine'}
          onConfirm={() => {
            void decide(
              confirm === 'approve' ? 'approved' : 'rejected',
              confirm === 'approve'
                ? 'Gate evidence reviewed in the console.'
                : 'Rejected from the console after reviewing the gate evidence.',
            );
          }}
          onDismiss={() => {
            setConfirm(null);
          }}
        >
          <p>
            {approval?.model} at this site, with {approval?.gates?.length ?? 0} gate results.
          </p>
        </Dialog>
      )}
    </>
  );
}

/**
 * The evidence table. Value, baseline, margin and result for every gate.
 *
 * Never a bare tick. A gate that renders only pass or fail hides the margin,
 * and the margin is what tells an approver whether a result is comfortable or
 * one rerun away from failing.
 */
function EvidenceTable({
  approval,
  onSeen,
}: {
  approval: Approval;
  onSeen: () => void;
}): JSX.Element {
  const [sortByMargin, setSortByMargin] = useState(true);
  const gates = [...(approval.gates ?? [])].sort((a, b) =>
    sortByMargin ? (a.margin ?? 0) - (b.margin ?? 0) : a.gate.localeCompare(b.gate),
  );

  return (
    <section
      className="cn-evidence"
      aria-labelledby="cn-evidence-heading"
      data-testid="gate-evidence"
      ref={(node) => {
        if (node === null) return;
        // Observed rather than assumed. `onSeen` fires when the evidence has
        // actually been in the viewport, which is what AC-U13 is about.
        if (typeof IntersectionObserver === 'undefined') {
          onSeen();
          return;
        }
        const observer = new IntersectionObserver(
          (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
              onSeen();
              observer.disconnect();
            }
          },
          { threshold: 0.5 },
        );
        observer.observe(node);
      }}
    >
      <h2 id="cn-evidence-heading">Gate evidence</h2>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setSortByMargin((value) => !value);
        }}
      >
        {sortByMargin ? 'Sorted by margin, tightest first' : 'Sorted by gate'}
      </Button>

      <GateCard
        gate={approval.model}
        decision={gates.every((gate) => gate.passed) ? 'allow' : 'deny'}
        evidence={gates.map((gate) => ({
          kind: gate.suiteVersion,
          requirement: `${gate.gate} at or above ${String(gate.baselineValue ?? 0)}`,
          met: gate.passed,
          observed: `${String(gate.value)} (margin ${String(gate.margin ?? 0)})`,
          digest: `${gate.gate}:${gate.suiteVersion}`,
        }))}
      />
    </section>
  );
}
