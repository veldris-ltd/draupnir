import type { JSX } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { JARNGREIPR_VERSION } from './index';

/**
 * The one story that exists before the design system does.
 *
 * It is here so that Storybook builds, the accessibility addon runs and the
 * visual regression project has something to diff. Prompt UX-1 replaces it
 * with the token gallery.
 */
function Harness(): JSX.Element {
  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem' }}>
      <h1>JARNGREIPR {JARNGREIPR_VERSION}</h1>
      <p>The design system harness is wired. No components yet.</p>
    </main>
  );
}

const meta = {
  title: 'Harness/Placeholder',
  component: Harness,
  tags: ['autodocs'],
} satisfies Meta<typeof Harness>;

export default meta;

export const Default: StoryObj<typeof meta> = {};
