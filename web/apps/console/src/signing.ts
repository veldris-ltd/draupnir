/**
 * The approver's signing agent, as S13 reaches it. RF-40.
 *
 * "Sign and approve" sent a placeholder signature no key produced, and no
 * `decidedAt`, so the API refused every approval from the console. A browser
 * cannot sign arbitrary bytes with a key, so the approval is signed by
 * `draupnir.gleipnir.signing_agent`, running on the approver's own machine
 * with their key. The console asks it who it is before offering approval, and
 * says so plainly when nothing answers, rather than offering a control that
 * cannot succeed.
 *
 * The agent builds the signed bytes itself from the fields sent, with the
 * function the API verifies against, so there is no second implementation of
 * the payload here to drift from the first.
 */

/** Where the agent listens. The console's content security policy names it too. */
export const SIGNING_AGENT = 'http://127.0.0.1:47920';

/** Whose key the agent holds. */
export interface AgentIdentity {
  approver: string;
  keyId: string;
}

/** The fields of an approval the queue row carries for the current approver. */
export interface SigningFields {
  subject: string;
  approver: string;
  policyVersion: string;
  soleApproverException: boolean;
}

/** A signature, and the instant it was signed over. */
export interface Signed {
  signature: string;
  decidedAt: string;
}

export type AgentState =
  | { state: 'checking' }
  | { state: 'ready'; identity: AgentIdentity }
  | { state: 'unreachable'; reason: string };

/** Ask the agent who it is. Never throws: an absent agent is a state, not an error. */
export async function probeAgent(): Promise<AgentState> {
  try {
    const response = await fetch(`${SIGNING_AGENT}/v1/identity`, { method: 'GET' });
    if (!response.ok) {
      return {
        state: 'unreachable',
        reason: `The signing agent at ${SIGNING_AGENT} answered ${String(response.status)}.`,
      };
    }
    return { state: 'ready', identity: (await response.json()) as AgentIdentity };
  } catch {
    return {
      state: 'unreachable',
      reason: `No signing agent is answering at ${SIGNING_AGENT}.`,
    };
  }
}

/** Have the agent sign an approval built from these fields. */
export async function signApproval(fields: SigningFields): Promise<Signed> {
  const response = await fetch(`${SIGNING_AGENT}/v1/sign-approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...fields, decision: 'approved' }),
  });
  if (!response.ok) {
    const refusal = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(refusal.error ?? `The signing agent answered ${String(response.status)}.`);
  }
  return (await response.json()) as Signed;
}
