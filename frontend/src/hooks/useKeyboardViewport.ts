import { useEffect } from 'react';

/** Deltas below this are collapsible browser toolbars, not a keyboard. */
export const KEYBOARD_MIN_HEIGHT_PX = 150;

/** Keyboards never exceed 75% of the viewport; guards against rotation glitches. */
const MAX_INSET_RATIO = 0.75;

const MAX_PINCH_SCALE = 1.01;

/**
 * Tracks the on-screen keyboard and exposes its height as the
 * `--keyboard-inset` CSS variable on `:root`.
 *
 * - Android Chrome 108+ (viewport `interactive-widget=resizes-content`)
 *   resizes the layout viewport natively, so the computed inset stays 0.
 * - iOS Safari overlays the keyboard on a fixed layout viewport; the inset
 *   derived from `visualViewport` is what lifts the sticky footer above it.
 */
export function useKeyboardViewport(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) {
      return undefined;
    }

    let frame = 0;

    const applyInset = (): void => {
      frame = 0;

      const covered = Math.max(
        0,
        Math.round(window.innerHeight - viewport.height - viewport.offsetTop),
      );
      const capped = Math.round(Math.min(covered, window.innerHeight * MAX_INSET_RATIO));

      if (capped >= KEYBOARD_MIN_HEIGHT_PX && viewport.scale <= MAX_PINCH_SCALE) {
        document.documentElement.style.setProperty('--keyboard-inset', `${capped}px`);
      } else {
        document.documentElement.style.removeProperty('--keyboard-inset');
      }
    };

    const scheduleApply = (): void => {
      if (frame !== 0) {
        return;
      }
      frame = window.requestAnimationFrame(applyInset);
    };

    viewport.addEventListener('resize', scheduleApply);
    viewport.addEventListener('scroll', scheduleApply);
    window.addEventListener('orientationchange', scheduleApply);

    return () => {
      viewport.removeEventListener('resize', scheduleApply);
      viewport.removeEventListener('scroll', scheduleApply);
      window.removeEventListener('orientationchange', scheduleApply);
      if (frame !== 0) {
        window.cancelAnimationFrame(frame);
      }
      document.documentElement.style.removeProperty('--keyboard-inset');
    };
  }, []);
}