import { describe, expect, it } from 'vitest';
import { readSource } from './source';

/**
 * RF-36. The console calls the API same-origin, so whatever serves it has to
 * route the API's paths. Three things do: the Vite development proxy, the
 * static server the journeys run against, and the deployed nginx. RF-03 added
 * `/auth/login` to the API and to nginx, and the journey server kept its own
 * list of three prefixes, so the sign-in journey received the console's HTML
 * where it asserted a redirect.
 *
 * The two development servers now read one list. These tests hold them to it,
 * and hold the list to nginx, so a route added in one place and not the others
 * fails here rather than in a journey nobody runs.
 */

const PROXIED = JSON.parse(readSource('scripts/proxied-prefixes.json')) as string[];

/** Every `location` in the nginx configuration that passes to the API. */
function nginxProxiedLocations(): string[] {
  const config = readSource('../docker/nginx.conf');
  const locations: string[] = [];
  for (const match of config.matchAll(/location\s+(?:=\s+|~\s+)?(\S+)\s*\{([^}]*)\}/g)) {
    const [, path = '', body = ''] = match;
    if (body.includes('proxy_pass')) locations.push(path.replace(/^\^/, ''));
  }
  return locations;
}

function isUnder(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

describe('the paths the console’s servers route to the API', () => {
  it('include the sign-in flow', () => {
    expect(PROXIED).toContain('/auth');
    for (const prefix of ['/v1', '/healthz', '/readyz']) expect(PROXIED).toContain(prefix);
  });

  it.each([['apps/console/vite.config.ts'], ['scripts/serve-console.mjs']])(
    'are read by %s from the one list, not written into it',
    (file) => {
      const source = readSource(file);
      expect(source).toContain('proxied-prefixes.json');
      // A literal prefix is a second copy of the list, which is the defect.
      for (const prefix of PROXIED) expect(source).not.toContain(`'${prefix}'`);
    },
  );

  it('are the paths the deployed nginx passes to the API', () => {
    const locations = nginxProxiedLocations();
    expect(locations.length).toBeGreaterThan(0);

    for (const prefix of PROXIED) {
      expect(
        locations.some((location) => isUnder(location, prefix)),
        `nginx does not proxy ${prefix}`,
      ).toBe(true);
    }
    for (const location of locations) {
      expect(
        PROXIED.some((prefix) => isUnder(location, prefix)),
        `nginx proxies ${location}, which the development servers do not`,
      ).toBe(true);
    }
  });

  it('do not include the loopback-only metrics endpoint', () => {
    // nginx refuses `/metrics` on purpose (SAD 8.1); a development server that
    // proxied it would be the one place it was reachable through the console.
    expect(PROXIED).not.toContain('/metrics');
  });
});
