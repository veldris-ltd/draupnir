import { describe, expect, it } from 'vitest';
import { decide, missingBaselineMessage } from '../e2e/visual/baseline';

/**
 * What the visual gate does about a baseline that is not there. RF-22.
 *
 * The spec recorded a missing baseline and passed, always. Sound reasoning —
 * a new platform should not go red through no fault of the change under test —
 * and the consequence was that the gate never ran where it mattered: every
 * committed baseline is `…-win32.png` and CI runs on `ubuntu-24.04-arm`, so
 * every CI run recorded 220 baselines into a container it then discarded and
 * reported stage 2.9 green having compared nothing.
 *
 * A gate that cannot fail is worse than no gate, because it occupies the place
 * where somebody would otherwise notice the absence. These are the cases that
 * distinguish the four situations, and the reason `decide` is a function at
 * all: the Playwright spec is the one place a unit test cannot reach, so the
 * decision was in the only part of the system that could not be checked.
 */

describe('deciding what to do about a missing visual baseline', () => {
  it('compares when the baseline is there, wherever it is running', () => {
    expect(decide({ exists: true, ci: true, bootstrap: false })).toBe('compare');
    expect(decide({ exists: true, ci: false, bootstrap: false })).toBe('compare');
  });

  it('fails on CI when the baseline is missing', () => {
    expect(decide({ exists: false, ci: true, bootstrap: false })).toBe('fail');
  });

  it('records on a developer machine when the baseline is missing', () => {
    // A designer adding a story should get a file to look at and commit, not
    // a red run telling them to go and dispatch a workflow.
    expect(decide({ exists: false, ci: false, bootstrap: false })).toBe('record');
  });

  it('records on CI only when the run exists to generate baselines', () => {
    // The `visual-baselines` dispatch, which uploads them as an artefact for a
    // human to commit. A separate signal from CI rather than a mode CI falls
    // into: if recording were what CI did when it found nothing to compare
    // against, this finding would simply return.
    expect(decide({ exists: false, ci: true, bootstrap: true })).toBe('record');
  });

  it('never overwrites a baseline that exists, even while bootstrapping', () => {
    // Otherwise a dispatch run would regenerate every baseline from the
    // current code, which is a gate approving whatever it was pointed at.
    expect(decide({ exists: true, ci: true, bootstrap: true })).toBe('compare');
  });
});

describe('the message a red pipeline shows', () => {
  const message = missingBaselineMessage('Composites / CapacityGauge / Empty', 'linux');

  it('names the story and the platform', () => {
    expect(message).toContain('Composites / CapacityGauge / Empty');
    expect(message).toContain('linux');
  });

  it('says the reader did not cause it', () => {
    // The cause is a file that has never existed. Without this the reader goes
    // looking through their own diff for a change they did not make.
    expect(message).toContain('not something your change did');
  });

  it('names the workflow that fixes it and where to put the result', () => {
    expect(message).toContain('visual-baselines');
    expect(message).toContain('storybook.spec.ts-snapshots');
  });
});
