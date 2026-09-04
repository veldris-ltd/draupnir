import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DensityProvider, ThemeProvider } from '@draupnir/jarngreipr';
import { App } from './App';
import './index.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('#root is missing from index.html');
}

/**
 * The two providers sit above everything and below nothing.
 *
 * Each writes one attribute on `<html>` and the cascade does the rest, so no
 * screen and no component knows which theme or which density is in force. That
 * is the exit condition of prompt UX-1 stated as code: switching either one
 * changes nothing below this file.
 *
 * `user` is not passed yet. The choice is stored against a shared key until the
 * console holds a session, at which point the subject goes here and CON-A and
 * CON-B stop handing one operator's preference to the next person at the same
 * keyboard.
 */
createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <DensityProvider>
        <App />
      </DensityProvider>
    </ThemeProvider>
  </StrictMode>,
);
