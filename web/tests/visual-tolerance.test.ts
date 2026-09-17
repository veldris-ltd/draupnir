import { describe, expect, it } from 'vitest';
import config from '../playwright.config';

/**
 * What the visual gate is allowed to forgive. RF-47.
 *
 * RF-22 made stage 2.9 compare, and the comparison still passed the thing it
 * exists to catch: `maxDiffPixelRatio: 0.01` is 9,344 pixels of licence on a
 * 1280x730 story, so a component one pixel taller than its token went green
 * on CI. Reaching the budget took a 40px change, at ratio 0.042. A gate whose
 * tolerance is wider than its defects occupies the place where somebody would
 * otherwise notice.
 *
 * The number is asserted rather than merely commented because a budget is the
 * kind of thing that comes back: it is one line, it makes a red run green, and
 * the reason it is zero lives in a commit message nobody reads at the moment
 * they are tempted. `maxDiffPixels` is checked too, since it is the same
 * licence counted absolutely, and either one alone would let the other in.
 *
 * The config object is imported rather than read as text: a regular expression
 * over the source would pass just as happily on a commented-out line.
 */

describe('the visual gate grants no pixel budget', () => {
  const screenshot = config.expect?.toHaveScreenshot;

  it('forgives no proportion of the image', () => {
    expect(screenshot?.maxDiffPixelRatio).toBe(0);
  });

  it('forgives no absolute count of pixels either', () => {
    // Unset is the default of 0, and that is the state the config is in; what
    // must not appear is a number above it.
    expect(screenshot?.maxDiffPixels ?? 0).toBe(0);
  });

  it('still compares per pixel perceptually, not byte for byte', () => {
    // Left at Playwright's default on purpose. An exact colour match would
    // fail on anti-aliasing that nobody can see, and a gate that cries wolf
    // gets switched off -- which is how stage 2.9 came to compare nothing in
    // the first place.
    expect(screenshot?.threshold).toBeUndefined();
  });
});
