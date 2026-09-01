import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { JARNGREIPR_VERSION } from '@draupnir/jarngreipr';
import type { Health } from '@draupnir/api-client';

/**
 * The application shell.
 *
 * It holds one thing: proof that the console, the generated client and the API
 * are actually connected. The information architecture of UX section 7 and the
 * thirty one screens of UX section 8 arrive in Prompt UX-4.
 */
export function App(): JSX.Element {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/healthz', { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<Health>) : null))
      .then(setHealth)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'unreachable');
        }
      });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <main>
      <h1>DRAUPNIR</h1>
      <p>CIM-56 model factory control plane.</p>
      <dl>
        <dt>Console</dt>
        <dd>0.1.0</dd>
        <dt>JARNGREIPR</dt>
        <dd>{JARNGREIPR_VERSION}</dd>
        <dt>API</dt>
        <dd data-testid="api-status">
          {health ? `${health.status}, site ${health.site_id}` : (error ?? 'checking')}
        </dd>
      </dl>
    </main>
  );
}
