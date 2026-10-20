/**
 * One gate result, and the pixel-threshold rule every pixel gate shares.
 *
 * Shape (schema spinegen-qa/1): {id, pass, value, max, hard, worst?, note?,
 * items?, data?, skipped?}. `hard` gates decide the exit code; a soft gate
 * (a warning) is reported but never fails the run; a skipped gate passes
 * and says why in `note`. `items` lists individual findings (capped) so a
 * reviewer sees WHICH mesh or constraint, not just how many.
 */

const MAX_ITEMS = 60;

export function makeGate(id, { value, max, hard = true, pass, worst, note, items, data, skipped = false }) {
  const g = { id, pass: pass ?? (skipped || value <= max), value, max, hard };
  if (skipped) g.skipped = true;
  if (worst !== undefined) g.worst = worst;
  if (note !== undefined) g.note = note;
  if (items && items.length) {
    g.items = items.slice(0, MAX_ITEMS);
    if (items.length > MAX_ITEMS) g.items.push(`... ${items.length - MAX_ITEMS} more`);
  }
  if (data !== undefined) g.data = data;
  return g;
}

export const skippedGate = (id, note, max = 0) => makeGate(id, { value: null, max, skipped: true, note });

/**
 * SOTA §5: pixel thresholds hold for H ≈ 900-1800 px at 1:1 and scale
 * linearly outside that band, so a 450 px mascot gets half the tolerance and
 * a 3600 px one double.
 */
export function pixelScale(H) {
  if (!(H > 0)) return 1;
  if (H < 900) return H / 900;
  if (H > 1800) return H / 1800;
  return 1;
}

/** Round for reporting only (the gates compare unrounded values). */
export const round = (x, digits = 4) => (Number.isFinite(x) ? +x.toFixed(digits) : x);
