#!/usr/bin/env node
/**
 * Serve the built Storybook for the accessibility and visual projects.
 *
 * Not `storybook dev`. The dev server compiles on demand, and eight parallel
 * shards navigating a hundred and sixty eight stories through it contend badly
 * enough to blow a ten minute timeout -- which reads as a broken component
 * rather than as a busy bundler. The static build is what a snapshot should be
 * taken against anyway: it is the artefact, it does not recompile mid-run, and
 * two runs of it are byte-identical.
 *
 * Written out rather than pulled in, because a static file server is thirty
 * lines and a dependency in the test path is a dependency in the supply chain.
 */

import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(
  fileURLToPath(new URL('..', import.meta.url)),
  process.argv[2] ?? 'storybook-static',
);
const PORT = Number(process.env.DRAUPNIR_STORYBOOK_PORT ?? 6006);

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

if (!existsSync(ROOT)) {
  process.stderr.write(
    `serve-storybook: ${ROOT} does not exist. Run \`pnpm run build-storybook\` first.\n`,
  );
  process.exit(1);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://localhost:${String(PORT)}`);
  const requested = decodeURIComponent(url.pathname);
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
    response.writeHead(404).end('Not found');
    return;
  }

  response.writeHead(200, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
    'cache-control': 'no-store',
  });
  createReadStream(file).pipe(response);
});

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`serve-storybook: ${ROOT} on http://127.0.0.1:${String(PORT)}\n`);
});
