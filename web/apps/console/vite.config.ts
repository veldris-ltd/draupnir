import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The console never learns the API's address. It calls same-origin paths, and
// the reverse proxy in front of both routes them in every environment. The
// prefixes are the ones `scripts/serve-console.mjs` routes for the journeys,
// read from the same file, so a route added to one server is added to both
// (RF-36).
const PROXIED = JSON.parse(
  readFileSync(new URL('../../scripts/proxied-prefixes.json', import.meta.url), 'utf8'),
) as string[];

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      PROXIED.map((prefix) => [prefix, { target: 'http://127.0.0.1:8000', changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
