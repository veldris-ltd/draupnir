import type { JSX } from 'react';
import { Badge, Button } from '@draupnir/jarngreipr';
import { useResource } from '../api/useResource';
import { PageHeading } from './parts';

/**
 * S29 Sign in.
 *
 * There is no password field on this screen and there never will be. MEGINGJORD
 * is the identity provider (SAD 5.2), authentication is OIDC, and the console
 * never sees a credential — it is redirected, and comes back with a token the
 * API verifies against MEGINGJORD's JWKS. A console that collected a password
 * would be a console that could leak one.
 *
 * What the screen does carry is the second factor requirement, stated before
 * the redirect rather than discovered after it. An approver must have
 * authenticated with a hardware factor (SAD 9.4, AC-S15): somebody who signs
 * in with a password alone can read, and will find the decision controls
 * unavailable when they reach the gate queue. Saying so here is the difference
 * between a rule and an ambush.
 */
export function SignIn(): JSX.Element {
  const health = useResource('getHealth', {});
  const returnTo = typeof window === 'undefined' ? '/' : window.location.pathname;

  return (
    <>
      <PageHeading title="Sign in" subtitle="DRAUPNIR control plane" />

      <section className="cn-signin" aria-labelledby="cn-signin-heading">
        <h2 id="cn-signin-heading">Authenticate with MEGINGJORD</h2>

        <p>
          Sign in is by OIDC against MEGINGJORD. This console never receives your credential: it
          redirects you to the identity provider and receives a token, which the API verifies
          against MEGINGJORD&rsquo;s signing keys on every request.
        </p>

        <dl className="jg-facts">
          <dt>Site</dt>
          <dd data-testid="signin-site">{health.data?.siteId ?? 'not yet known'}</dd>
          <dt>Control plane</dt>
          <dd>{health.data?.version == null ? 'unreachable' : `ALVISS ${health.data.version}`}</dd>
          <dt>Status</dt>
          <dd>
            {health.state === 'ready' ? (
              <Badge tone="success">reachable</Badge>
            ) : health.state === 'loading' ? (
              <Badge tone="neutral">checking</Badge>
            ) : (
              <Badge tone="danger">
                unreachable
                <span className="jg-sr-only">
                  . Signing in will not succeed until the control plane answers.
                </span>
              </Badge>
            )}
          </dd>
        </dl>

        <section className="cn-signin__factor" aria-labelledby="cn-factor-heading">
          <h3 id="cn-factor-heading">A hardware factor is required to approve</h3>
          <p>
            Reading and operating need a password and any second factor.{' '}
            <strong>Signing a release needs a hardware backed one</strong> — a security key or a
            platform authenticator. If you sign in without it you will be able to read the gate
            queue and the evidence, and the decision controls will be unavailable with that reason
            given. This is stated here rather than at the gate so it is not a surprise at the moment
            you need to act (SAD 9.4, AC-S15).
          </p>
        </section>

        <Button
          onClick={() => {
            // A full navigation, not a fetch: the OIDC flow is a browser
            // redirect and an XHR to the authorisation endpoint cannot complete
            // one.
            window.location.assign(`/auth/login?return_to=${encodeURIComponent(returnTo)}`);
          }}
        >
          Continue to MEGINGJORD
        </Button>

        <p className="cn-note">
          Your session is warned before it expires and work in progress is preserved: a
          specification being composed is never lost to a token refresh (UX 11).
        </p>
      </section>
    </>
  );
}
