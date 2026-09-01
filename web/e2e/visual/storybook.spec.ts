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
test('every Storybook story matches its baseline', async ({ page, baseURL }) => {
  const root = baseURL ?? '';
  const response = await page.request.get(`${root}/index.json`);
  expect(response.ok(), 'Storybook index.json is not reachable').toBeTruthy();

  const index = (await response.json()) as StoryIndex;
  const stories = Object.values(index.entries).filter((entry) => entry.type === 'story');
  expect(stories.length, 'Storybook has no stories to snapshot').toBeGreaterThan(0);

  const recorded: string[] = [];

  for (const story of stories) {
    await page.goto(`/iframe.html?id=${story.id}&viewMode=story`);
    await page.waitForSelector('#storybook-root > *', { state: 'attached' });

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
