import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

/**
 * Read a file from the workspace as text, for tests that assert on source.
 *
 * Not `import ... ?raw`: Vitest stubs CSS imports to the empty string, so a
 * stylesheet imported that way silently arrives blank and every assertion
 * against it passes vacuously. That is a worse failure than not having the
 * test, so the file is read from disk.
 *
 * Not `new URL(..., import.meta.url)` either: under the Vitest module runner
 * `import.meta.url` is an http URL, which `fileURLToPath` rejects. So the
 * workspace root is found by walking up for `pnpm-workspace.yaml`, which makes
 * the helper independent of the directory Vitest was started in.
 */
export function readSource(relative: string): string {
  // Normalised to LF: a Windows checkout with `core.autocrlf` on would
  // otherwise break every assertion that spans a line break, for a reason
  // that has nothing to do with what is being tested.
  const text = readFileSync(join(workspaceRoot(), relative), 'utf8');
  return text.split('\r\n').join('\n');
}

let cached: string | undefined;

/** The `web/` directory, found by walking up from the working directory. */
export function workspaceRoot(): string {
  if (cached !== undefined) return cached;
  let directory = resolve(process.cwd());
  for (;;) {
    if (existsSync(join(directory, 'pnpm-workspace.yaml'))) {
      cached = directory;
      return directory;
    }
    const parent = dirname(directory);
    if (parent === directory) {
      throw new Error(
        'Could not find pnpm-workspace.yaml above the working directory; ' +
          'readSource needs the web workspace root.',
      );
    }
    directory = parent;
  }
}
