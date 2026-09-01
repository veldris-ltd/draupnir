import type { Preview } from '@storybook/react';

const preview: Preview = {
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
