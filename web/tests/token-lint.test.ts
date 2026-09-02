import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { workspaceRoot } from './source';

/**
 * The token linter, checked against fixtures that are supposed to fail.
 *
 * AC-U3 is enforced by `scripts/token-lint.mjs`, and a gate that has never
 * been watched fail is a gate nobody knows works. The first version of this
 * linter passed the whole workspace and also passed a file containing
 * `color: #2d6cdf`, because its parser read one declaration per line and the
 * fixture wrote the rule on one. These fixtures exist so that cannot happen
 * again quietly.
 */

const FIXTURES = join(workspaceRoot(), 'tests', '__fixtures__', 'token-lint');

function run(root: string): { status: number; stdout: string; stderr: string } {
  const result = spawnSync(
    process.execPath,
    [join(workspaceRoot(), 'scripts', 'token-lint.mjs'), root],
    { encoding: 'utf8' },
  );
  return {
    status: result.status ?? -1,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}

describe('the token linter', () => {
  const failed = run(FIXTURES);

  it('fails on a fixture that states hard-coded values', () => {
    expect(failed.status).toBe(1);
  });

  it.each([
    ['colour', 'a hex colour', /violations\.css:8\s+colour/],
    ['spacing', 'a padding in pixels', /violations\.css:9\s+spacing/],
    ['radius', 'a border radius in pixels', /violations\.css:10\s+radius/],
    ['colour', 'an rgba() background', /violations\.css:11\s+colour/],
    ['colour', 'a named colour in a border shorthand', /violations\.css:12\s+colour/],
    ['token', 'a custom property minted outside the ramp', /violations\.css:13\s+token/],
  ])('catches %s: %s', (_category, _what, pattern) => {
    expect(failed.stderr).toMatch(pattern);
  });

  it('does not flag a rule written entirely in tokens', () => {
    // `.probe-ok` in the fixture and the whole of compliant.css. If either
    // were reported the linter would be unusable and would be turned off,
    // which is the usual way a lint rule stops protecting anything.
    expect(failed.stderr).not.toMatch(/compliant\.css/);
    expect(failed.stderr).not.toMatch(/violations\.css:14/);
  });

  it('does not flag intrinsic sizing', () => {
    // `height` and `max-width` are outside the three categories AC-U3 names.
    // Stated here so the omission stays a decision rather than becoming a gap.
    expect(failed.stderr).not.toMatch(/1\.25rem|46ch/);
  });

  it('passes the design system and the console', () => {
    const clean = run(workspaceRoot());
    expect(clean.stderr, clean.stderr).toBe('');
    expect(clean.status).toBe(0);
    expect(clean.stdout).toMatch(/no hard-coded colour, spacing or radius/);
  });
});
