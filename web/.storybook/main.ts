import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  stories: ['../packages/*/src/**/*.mdx', '../packages/*/src/**/*.stories.@(ts|tsx)'],
  addons: ['@storybook/addon-essentials', '@storybook/addon-a11y'],
  framework: {
    name: '@storybook/react-vite',
    options: {},
  },
  typescript: {
    check: false,
    reactDocgen: 'react-docgen-typescript',
  },
  docs: { autodocs: 'tag' },
  // DRAUPNIR is an internal system on an isolated estate. Nothing about the
  // component library leaves it, including usage telemetry.
  core: { disableTelemetry: true },
};

export default config;
