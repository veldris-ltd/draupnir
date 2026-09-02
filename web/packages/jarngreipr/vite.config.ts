import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// A library build, not an application build: React stays external so the
// console ships one copy.
export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: resolve(import.meta.dirname, 'src/index.ts'),
      formats: ['es'],
      fileName: () => 'index.js',
      // Vite 6 takes this on `lib`, not on `build`. It was on `build`, where
      // it was silently ignored and the stylesheet came out as `style.css`
      // while package.json promised `jarngreipr.css`. Typechecking the config
      // files is what found it.
      cssFileName: 'jarngreipr',
    },
    rollupOptions: {
      external: ['react', 'react-dom', 'react/jsx-runtime'],
    },
    sourcemap: true,
    emptyOutDir: true,
  },
});
