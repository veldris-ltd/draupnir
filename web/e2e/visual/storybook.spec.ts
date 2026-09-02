import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { test, expect } from '@playwright/test';

interface StoryIndex {
  entries: Record<string, { id: string; title: string; name: string; type: string }>;
}

// SAD 11E.3: Storybook snapshots of every design system component in every
// state, with a diff gate. The spec enumerates the story index rather than
// naming stories, so a component added without a snapshot is impossible.
//
// Baselines are per platform, and Playwright's own behaviour for a missing one
// is to write it and fail. That would make the first build on any new platform
// red through no fault of the change under test, so a missing baseline is
// recorded and annotated here instead. The annotation names the file to
// commit; every run after that is a real diff gate.
//
// Sharded, because a hundred and sixty eight navigations and full-page
// screenshots in series take a quarter of an hour on one core. Each shard
// discovers the index itself and takes every Nth story, so nothing has to know
// the story list before the run starts.
const SHARDS = 8;

for (let shard = 0; shard < SHARDS; shard += 1) {
  test(`Storybook baselines, shard ${String(shard + 1)} of ${String(SHARDS)}`, async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(10 * 60 * 1000);

    const root = baseURL ?? '';
    const response = await page.request.get(`${root}/index.json`);
    expect(response.ok(), 'Storybook index.json is not reachable').toBeTruthy();

    const index = (await response.json()) as StoryIndex;
    const all = Object.values(index.entries)
      .filter((entry) => entry.type === 'story')
      .sort((a, b) => a.id.localeCompare(b.id));
    expect(all.length, 'Storybook has no stories to snapshot').toBeGreaterThan(0);

    const stories = all.filter((_, position) => position % SHARDS === shard);
    expect(stories.length, `shard ${String(shard)} has no stories`).toBeGreaterThan(0);

    const recorded: string[] = [];

    // "Storybook builds and every story renders" is the exit condition, and a
    // built bundle proves only the first half. Storybook catches a story's
    // exception and renders its own message, so a broken story still produces
    // a screenshot and a green snapshot run. It is caught here instead: an
    // uncaught exception, or the body carrying `sb-show-errordisplay` rather
    // than `sb-show-main`.
    //
    // This is not hypothetical. The preview decorator's JSX compiles with the
    // classic runtime, which needs `React` in scope; without the import every
    // one of the 168 stories rendered an error boundary and the build still
    // reported success.
    const thrown: string[] = [];
    page.on('pageerror', (error) => {
      thrown.push(error.message);
    });

    for (const story of stories) {
      const before = thrown.length;
      await page.goto(`/iframe.html?id=${story.id}&viewMode=story`);
      await page.waitForSelector('#storybook-root > *', { state: 'attached' });

      expect(thrown.slice(before), `${story.title} / ${story.name} threw while rendering`).toEqual(
        [],
      );
      // Not `#error-message`: that element is in the iframe template
      // unconditionally and merely hidden, so testing for its presence marks
      // every story as broken.
      await expect(
        page.locator('body'),
        `${story.title} / ${story.name} did not render`,
      ).toHaveClass(/sb-show-main/);

      const name = `${story.id}.png`;
      const baseline = test.info().snapshotPath(name);

      if (existsSync(baseline)) {
        await expect(page).toHaveScreenshot(name, { fullPage: true });
        continue;
      }

      mkdirSync(dirname(baseline), { recursive: true });
      writeFileSync(baseline, await page.screenshot({ fullPage: true, animations: 'disabled' }));
      recorded.push(baseline);
    }

    if (recorded.length > 0) {
      test.info().annotations.push({
        type: 'baseline-recorded',
        description:
          `${String(recorded.length)} visual baseline(s) had none for this platform and were ` +
          `recorded. Commit them so that later builds diff against them:\n` +
          recorded.join('\n'),
      });
    }
  });
}
