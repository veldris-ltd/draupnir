import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';

interface StoryIndex {
  entries: Record<string, { id: string; title: string; name: string; type: string }>;
}

const STORYBOOK = process.env.DRAUPNIR_STORYBOOK_URL ?? 'http://127.0.0.1:6006';

/**
 * WCAG 2.2 AA at the component level, which is what a route-level sweep cannot
 * give you.
 *
 * A component that only ever appears on a route in its happy path is a
 * component whose denied and partitioned states nothing has ever run axe over
 * -- and those are the states most likely to be wrong, because they are the
 * ones nobody looks at while building. Storybook renders all seven states of
 * every component, so this is the surface where the six states are actually
 * checked.
 *
 * Serious and critical are the gate (SAD 11F.4, Decision S13). Moderate and
 * minor findings are annotated so they are visible without blocking a merge.
 *
 * Sharded rather than one sweeping test. Loading a story and injecting axe
 * costs a few seconds, and a hundred and sixty eight of those in series is a
 * quarter of an hour in which the pipeline is doing one thing on one core.
 * Each shard discovers the full index and takes every Nth story, so no shard
 * needs the story list before the run starts and adding a component does not
 * mean editing this file.
 */
const SHARDS = 8;

for (let shard = 0; shard < SHARDS; shard += 1) {
  test(`axe over Storybook stories, shard ${String(shard + 1)} of ${String(SHARDS)}`, async ({
    page,
  }) => {
    test.setTimeout(10 * 60 * 1000);

    const response = await page.request.get(`${STORYBOOK}/index.json`);
    expect(response.ok(), 'Storybook index.json is not reachable').toBeTruthy();

    const index = (await response.json()) as StoryIndex;
    const all = Object.values(index.entries)
      .filter((entry) => entry.type === 'story')
      .sort((a, b) => a.id.localeCompare(b.id));
    expect(all.length, 'Storybook has no stories to check').toBeGreaterThan(0);

    const stories = all.filter((_, position) => position % SHARDS === shard);
    expect(stories.length, `shard ${String(shard)} has no stories`).toBeGreaterThan(0);

    const blocking: string[] = [];
    const advisory: string[] = [];

    for (const story of stories) {
      await page.goto(`${STORYBOOK}/iframe.html?id=${story.id}&viewMode=story`);
      await page.waitForSelector('#storybook-root > *', { state: 'attached' });

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .include('#storybook-root')
        .analyze();

      for (const violation of results.violations) {
        const where = `${story.title} / ${story.name}: ${violation.id} (${String(violation.impact)})`;
        if (violation.impact === 'serious' || violation.impact === 'critical') {
          blocking.push(
            `${where}\n    ${violation.help}\n    ${violation.nodes
              .slice(0, 3)
              .map((node) => node.target.join(' '))
              .join('\n    ')}`,
          );
        } else {
          advisory.push(where);
        }
      }
    }

    if (advisory.length > 0) {
      test.info().annotations.push({
        type: 'a11y-advisory',
        description: `${String(advisory.length)} moderate or minor finding(s):\n${advisory.join('\n')}`,
      });
    }

    expect(
      blocking,
      `${String(blocking.length)} serious or critical accessibility violation(s):\n${blocking.join('\n\n')}`,
    ).toEqual([]);
  });
}
