/**
 * What to do about a visual baseline that is not there. RF-22.
 *
 * The spec used to record a missing baseline and pass, always. The reasoning
 * was sound as far as it went — a missing baseline should not make the first
 * build on a new platform red through no fault of the change under test — but
 * every one of the 220 committed baselines is `…-win32.png` and CI runs on
 * `ubuntu-24.04-arm`. So on every CI run every story had no baseline, 220 were
 * written into a container that was then thrown away, and stage 2.9 reported
 * green having compared nothing.
 *
 * A gate that cannot fail is worse than no gate. It occupies the place where
 * somebody would otherwise notice the absence.
 *
 * The decision is a function rather than three branches inside a Playwright
 * test because it is the part with the defect in it, and a Playwright test is
 * the one place a unit test cannot reach.
 */

/** What the spec should do with one story's screenshot. */
export type Decision = 'compare' | 'record' | 'fail';

export interface Circumstances {
  /** Whether a baseline for this story exists on this platform. */
  readonly exists: boolean;
  /** Whether this is a pipeline run. `process.env.CI` in practice. */
  readonly ci: boolean;
  /**
   * Whether this run exists to *create* baselines: the one-off
   * `visual-baselines` workflow dispatch, which uploads them for a human to
   * commit. Deliberately a separate signal from `ci` rather than a mode CI
   * falls into, so that recording can never be what an ordinary pipeline run
   * does when it finds nothing to compare against.
   */
  readonly bootstrap: boolean;
}

/**
 * Decide, given the circumstances.
 *
 * On a developer machine a missing baseline is still recorded: a designer
 * adding a story should get a file to look at and commit, not a red run
 * telling them to go and dispatch a workflow.
 */
export function decide({ exists, ci, bootstrap }: Circumstances): Decision {
  if (exists) {
    return 'compare';
  }
  if (bootstrap) {
    return 'record';
  }
  return ci ? 'fail' : 'record';
}

/**
 * What to tell somebody whose CI run just went red for want of a baseline.
 *
 * Long, and deliberately: the person reading it did not break anything, the
 * cause is a file that has never existed, and the remedy is a workflow they
 * have probably never run. A message that said "snapshot missing" would send
 * them looking through their own diff.
 */
export function missingBaselineMessage(story: string, platform: string): string {
  return [
    `No visual baseline for ${story} on ${platform}, and this is a pipeline run.`,
    '',
    'This is not something your change did. Baselines are per platform, and a',
    'platform with none has never had them generated.',
    '',
    'To generate them: run the `visual-baselines` workflow (Actions → Visual',
    'baselines → Run workflow). It records every story on this architecture and',
    'uploads them as an artefact. Download it, unpack it into',
    'web/e2e/visual/storybook.spec.ts-snapshots/, and commit.',
    '',
    'A human commits them on purpose. Nothing writes baselines to a branch on',
    'its own: a bot that regenerates a baseline whenever one is missing is a',
    'gate that approves every change to the thing it is meant to be guarding.',
  ].join('\n');
}
