// Flat config.
//
// Type-aware linting needs every linted TypeScript file to belong to a
// project, so the package tsconfigs are named explicitly alongside
// tsconfig.tools.json, which covers the configuration, specs and stories that
// no package emits.
//
// The token linter of UX-0 lands with the design system in Prompt UX-1; the
// terminology rule below is the half of that pair which can be written before
// the tokens exist.
import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import storybook from 'eslint-plugin-storybook';

const TYPESCRIPT = ['**/*.ts', '**/*.tsx'];

export default tseslint.config(
  {
    ignores: [
      '**/dist/**',
      '**/dist-types/**',
      '**/storybook-static/**',
      '**/playwright-report/**',
      '**/test-results/**',
      '**/node_modules/**',
      'packages/api-client/src/generated/**',
    ],
  },

  // Configuration files are plain JavaScript and are linted without type
  // information; there is no project that could contain them.
  {
    files: ['**/*.js'],
    ...js.configs.recommended,
    languageOptions: { globals: { ...globals.node } },
  },

  {
    files: TYPESCRIPT,
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        project: [
          './tsconfig.tools.json',
          './apps/*/tsconfig.json',
          './packages/*/tsconfig.json',
        ],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-restricted-syntax': [
        'error',
        {
          // SAD 11A.2, Decision S12: a forge is a site, an appliance is a
          // node. Ambiguity in a naming scheme is paid for at three in the
          // morning, not at design time.
          selector: 'Literal[value=/\\bnodes?\\b/i][value=/forge|site|federation/i]',
          message:
            'A forge is a site, not a node (SAD 11A.2, Decision S12). "Node" means one appliance.',
        },
      ],
    },
  },

  ...storybook.configs['flat/recommended'],

  {
    files: ['**/*.config.ts', 'e2e/**/*.ts', 'tests/**/*.ts', '.storybook/**/*.ts'],
    rules: {
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
    },
  },
);
