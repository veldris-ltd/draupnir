import type { JSX } from 'react';
import type { Meta } from '@storybook/react';
import { RUN_STATES } from './index';
import { RunStatePill } from './RunStatePill';

/**
 * The run state tokens of section 4.2, all fourteen, in one place.
 *
 * This is the visual half of AC-V4. The test asserts that no component maps a
 * state to a colour; the snapshot asserts what the token layer's mapping
 * actually looks like, in both themes, so a change to a state colour is a
 * diff somebody has to approve rather than a change nobody sees.
 *
 * Not a six-state component: a pill has one state, the one it names. It is a
 * token rendered as markup, which is why it lives beside the tokens and not
 * among the primitives.
 */
export default {
  title: 'Tokens/Run state',
  parameters: { layout: 'padded' },
} satisfies Meta;

function Row({ theme }: { theme: 'light' | 'dark' }): JSX.Element {
  return (
    <div
      className="jg-root"
      data-jg-theme={theme}
      style={{ padding: 'var(--jg-space-4)', display: 'grid', gap: 'var(--jg-space-2)' }}
    >
      <p style={{ margin: 0, color: 'var(--jg-text-muted)' }}>{theme}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--jg-space-2)' }}>
        {RUN_STATES.map((state) => (
          <RunStatePill key={state} state={state} />
        ))}
      </div>
    </div>
  );
}

export const Light = { render: () => <Row theme="light" /> };
export const Dark = { render: () => <Row theme="dark" /> };
export const BothThemes = {
  render: () => (
    <>
      <Row theme="light" />
      <Row theme="dark" />
    </>
  ),
};
