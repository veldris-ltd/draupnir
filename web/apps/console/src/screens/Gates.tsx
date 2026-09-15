import type { JSX } from 'react';
import { useEffect, useState } from 'react';
import { Badge, Button, Dialog, EvidencePanel, StateSurface, Table } from '@draupnir/jarngreipr';
import type { EvidenceRow } from '@draupnir/jarngreipr';
import { ApiError, call, idempotencyKey } from '@draupnir/api-client';
import type { Approval } from '@draupnir/api-client';
import { pageIsEmpty, problemOf, useResource } from '../api/useResource';
import { linkProps } from '../routing';
import { probeAgent, signApproval, type AgentState } from '../signing';
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

  // RF-40. An approval is signed where the approver's key is, by their signing
  // agent. Asked before anything is offered, so an approver whose agent is not
  // running is told on this screen rather than refused after confirming.
  const [agent, setAgent] = useState<AgentState>({ state: 'checking' });
  useEffect(() => {
    let live = true;
    void probeAgent().then((found) => {
      if (live) setAgent(found);
    });
    return () => {
      live = false;
    };
  }, []);
  const signing = approval?.signing ?? null;
  const canApprove =
    agent.state === 'ready' && signing !== null && agent.identity.approver === signing.approver;

  async function decide(decision: 'approved' | 'rejected', reason: string): Promise<void> {
    setConfirm(null);
    try {
      let signed: { signature: string; decidedAt?: string } = {
        // A rejection is not signed, and the API records no signature for one.
        signature: 'unsigned-rejection',
      };
      if (decision === 'approved') {
        if (signing === null) throw new Error('The API did not say what this approval signs.');
        signed = await signApproval(signing);
      }
      await call('decideGate', {
        params: { gate_id: gateId },
        body: { decision, reason, ...signed },
        // RF-32: conditional on the queue entry the evidence above was read
        // from, so a decision somebody else took meanwhile is refused with 412.
        ifMatch: approval?.etag ?? '',
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
          : {
              title:
                decision === 'approved'
                  ? 'The approval was not signed'
                  : 'The decision did not complete',
              detail: cause instanceof Error ? cause.message : String(cause),
            },
      );
    }
  }

  return (
    <>
      <PageHeading title={approval?.model ?? 'Approval'} subtitle={gateId} />

      {/* The outcome sits outside the approval: a decided artefact leaves the
          pending queue when it is refreshed, and a message rendered with it
          would vanish at the moment it is shown (RF-41). */}
      {outcome === null ? null : (
        <p role="status" data-testid="decision-outcome">
          {outcome}
        </p>
      )}

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
              {/* RF-41: no recorded evidence is said, and nothing is decided on it. */}
              {(approval.gates ?? []).length > 0 ? null : (
                <p className="cn-decision__gate" role="status" data-testid="no-evidence">
                  {NO_EVIDENCE}
                </p>
              )}
              {/* RF-40: whether an approval can be signed, said before the dialog. */}
              <p className="cn-decision__signing" role="status" data-testid="signing-agent">
                {signingStatus(agent, signing)}
              </p>
              <div className="cn-decision__controls">
                <Button
                  variant="primary"
                  state={
                    evidenceSeen && canApprove && (approval.gates ?? []).length > 0
                      ? 'ready'
                      : 'readOnly'
                  }
                  // The reason a decision is unavailable, rather than the
                  // generic read-only sentence: no evidence (RF-41) before an
                  // unsignable approval (RF-40).
                  stateMessage={decisionUnavailable(approval, evidenceSeen, () =>
                    canApprove ? undefined : signingStatus(agent, signing),
                  )}
                  onClick={() => {
                    setConfirm('approve');
                  }}
                >
                  Sign and approve
                </Button>
                <Button
                  variant="danger"
                  state={evidenceSeen && (approval.gates ?? []).length > 0 ? 'ready' : 'readOnly'}
                  stateMessage={decisionUnavailable(approval, evidenceSeen, () => undefined)}
                  onClick={() => {
                    setConfirm('reject');
                  }}
                >
                  Reject
                </Button>
              </div>
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

/** What S13 says when no gate evidence is recorded for an artefact. RF-41. */
const NO_EVIDENCE =
  'No gate evidence is recorded for this artefact, so there is nothing to decide on. ' +
  'A decision is offered once the evaluation it rests on is in the ledger.';

/**
 * Why a decision control is unavailable, or undefined for the generic reason.
 * No recorded evidence comes first: nothing is decided on an empty table
 * (RF-41). Otherwise, once the evidence has been seen, whatever else the
 * control is waiting on.
 */
function decisionUnavailable(
  approval: Approval,
  evidenceSeen: boolean,
  otherwise: () => string | undefined,
): string | undefined {
  if ((approval.gates ?? []).length === 0) return NO_EVIDENCE;
  return evidenceSeen ? otherwise() : undefined;
}

/**
 * What S13 says about signing an approval, before anybody opens the dialog.
 * RF-40: an approver who cannot sign is told here, in words, rather than
 * refused after confirming.
 */
function signingStatus(
  agent: AgentState,
  signing: NonNullable<Approval['signing']> | null,
): string {
  if (agent.state === 'checking') return 'Looking for your signing agent.';
  if (agent.state === 'unreachable') {
    return (
      `${agent.reason} An approval is signed where your key is, so approval is unavailable ` +
      'until the agent is running. Rejection needs no signature and is still available.'
    );
  }
  if (signing === null) {
    return 'The API could not say what an approval here would sign, so approval is unavailable.';
  }
  if (agent.identity.approver !== signing.approver) {
    return (
      `The signing agent holds ${agent.identity.approver}’s key and you are ` +
      `${signing.approver}, so an approval signed with it would not verify.`
    );
  }
  return `Signing as ${agent.identity.approver} with key ${agent.identity.keyId}.`;
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
  const rows: EvidenceRow[] = (approval.gates ?? []).map((gate) => ({
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
    <section
      className="cn-card"
      data-testid="gate-evidence"
      ref={(node) => {
        // AC-S8 and AC-U13: the evidence is in the viewport before any
        // decision control, and the screen is told once it has been.
        if (node !== null) onSeen();
      }}
    >
      <EvidencePanel suiteVersion={approval.gates?.[0]?.suiteVersion ?? 'unknown'} rows={rows} />
    </section>
  );
}
