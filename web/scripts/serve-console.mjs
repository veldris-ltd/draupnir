#!/usr/bin/env node
/**
 * Serve the built console for the journey and accessibility projects.
 *
 * Not `vite dev`. The dev server injects the stylesheet through JavaScript, so
 * under parallel shards on a loaded runner there is a window where the markup
 * has rendered and the styles have not -- a table at its natural width, a
 * layout assertion that reads as a broken console rather than as a busy
 * bundler. That is the same contention `serve-storybook.mjs` exists to avoid.
 * The built console is also the artefact the journeys should run against: it
 * does not recompile mid-run, and two runs of it are byte-identical.
 *
 * This stands in for the reverse proxy that fronts the console in every real
 * environment. The console never learns the API's address -- it calls
 * same-origin `/v1`, `/healthz` and `/readyz` -- so something has to route
 * those, and in the dev server it was `server.proxy` in vite.config.ts. Here
 * it is the same three prefixes, to the same API.
 *
 * Written out rather than pulled in, for the reason serve-storybook.mjs gives:
 * a dependency in the test path is a dependency in the supply chain.
 */

import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer, request as httpRequest } from 'node:http';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(
  fileURLToPath(new URL('..', import.meta.url)),
  process.argv[2] ?? 'apps/console/dist',
);
const PORT = Number(process.env.DRAUPNIR_CONSOLE_PORT ?? 5173);
const API = new URL(process.env.DRAUPNIR_API_URL ?? 'http://127.0.0.1:8000');

// The three prefixes vite.config.ts proxies. Kept as prefixes rather than
// exact paths because `/v1` carries the whole API beneath it.
const PROXIED = ['/v1', '/healthz', '/readyz'];

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
};

if (!existsSync(join(ROOT, 'index.html'))) {
  process.stderr.write(
    `serve-console: ${ROOT} has no index.html. Run \`pnpm run build\` first.\n`,
  );
  process.exit(1);
}

/** Forward one request to the API, method, headers and body intact. */
function proxy(request, response) {
  const upstream = httpRequest(
    {
      protocol: API.protocol,
      hostname: API.hostname,
      port: API.port,
      method: request.method,
      path: request.url,
      // `host` must name the upstream, not this server, or the API's own URL
      // building and any host check see the wrong origin.
      headers: { ...request.headers, host: API.host },
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );

  upstream.on('error', (error) => {
    // Report it as a gateway failure rather than hanging until the test times
    // out: a dead API should not look like a slow console.
    response.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
    response.end(`serve-console: cannot reach ${API.origin}: ${error.message}`);
  });

  request.pipe(upstream);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${String(PORT)}`);
  const requested = decodeURIComponent(url.pathname);

  if (PROXIED.some((prefix) => requested === prefix || requested.startsWith(`${prefix}/`))) {
    proxy(request, response);
    return;
  }

  // Normalise before joining: `..` in a request path must not escape the root.
  const relative = normalize(requested).replace(/^([/\\])+/, '');
  if (relative.split(sep).includes('..')) {
    response.writeHead(403).end('Forbidden');
    return;
  }

  let file = join(ROOT, relative);
  if (!file.startsWith(ROOT)) {
    response.writeHead(403).end('Forbidden');
    return;
  }
  if (existsSync(file) && statSync(file).isDirectory()) {
    file = join(file, 'index.html');
  }

  if (!existsSync(file)) {
    // The console routes on the client, so an unknown path is a route and not
    // a missing file -- `try_files $uri $uri/ /index.html`, as the deployed
    // nginx does it. A path that names an extension is asking for an asset,
    // though, and answering that with HTML turns a missing bundle into a
    // parse error three layers away from its cause.
    if (extname(relative) !== '') {
      response.writeHead(404).end('Not found');
      return;
    }
    file = join(ROOT, 'index.html');
  }

  response.writeHead(200, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
    'cache-control': 'no-store',
  });
  createReadStream(file).pipe(response);
});

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`serve-console: ${ROOT} on http://127.0.0.1:${String(PORT)}\n`);
});
