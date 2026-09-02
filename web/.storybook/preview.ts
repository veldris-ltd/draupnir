import { createElement } from 'react';
import type { Preview } from '@storybook/react';
import '../packages/jarngreipr/src/styles.css';

/**
 * Every story renders inside `.jg-root`.
 *
 * The base class carries the surface, the type and -- through
 * `.jg-root :focus-visible` -- the one focus ring the whole system uses. A
 * story rendered outside it would be a component on the browser's default
 * white with no focus indicator, which is neither what ships nor what the
 * visual baseline should record.
 *
 * `createElement` rather than JSX, and so a `.ts` file rather than `.tsx`.
 * Storybook compiles the preview config with the classic JSX runtime, so JSX
 * here becomes a bare `React.createElement` call and fails at runtime with
 * "React is not defined" -- while the Storybook build still reports success and
 * every story quietly renders an error boundary. Writing the call out removes
 * the trap rather than working around it. The visual spec now checks that a
 * story actually rendered, so this cannot recur silently.
 *
 * The theme is a toolbar global rather than a story parameter so that every
 * story can be seen in both ramps without writing a second story: AC-U7 is
 * about both, and a dark theme nobody looks at is a dark theme nobody tests.
 */
const preview: Preview = {
  globalTypes: {
    theme: {
      description: 'Colour ramp',
      defaultValue: 'light',
      toolbar: {
        title: 'Theme',
        icon: 'contrast',
        items: [
          { value: 'light', title: 'Light' },
          { value: 'dark', title: 'Dark' },
        ],
        dynamicTitle: true,
      },
    },
  },

  decorators: [
    (Story, context) => {
      const theme = context.globals.theme === 'dark' ? 'dark' : 'light';
      // Set on the document element, because that is where the ramp is defined
      // and where a real console would set it.
      document.documentElement.setAttribute('data-jg-theme', theme);
      return createElement(
        'div',
        { className: 'jg-root', style: { padding: 'var(--jg-space-6)' } },
        createElement(Story),
      );
    },
  ],

  parameters: {
    controls: { expanded: true },
    // SAD 11E.3 and UX 12: zero serious or critical violations. The addon
    // surfaces them while a component is being built; the Playwright a11y
    // project is the gate.
    a11y: {
      config: {
        rules: [{ id: 'color-contrast', enabled: true }],
      },
    },
    backgrounds: { disable: true },
  },
};

export default preview;
