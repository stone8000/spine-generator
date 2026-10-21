/**
 * Side-fighter control for an Iron Jaw-style slot, Spine 4.3 runtime (@esotericsoftware/spine-pixi-v8 4.3.x, pixi.js >= 8.16).
 *
 * Track layout
 *   0  idle      2.0 s sine loop, never stopped
 *   1  reaction  punch / flinch clips, mixed in over track 0 and mixed back out to the idle when done
 *   2  breath    optional additive layer (Spine 4.3 TrackEntry.additive): small chest/shoulder motion that keeps
 *                going on top of whatever tracks 0 and 1 show, so a held reaction pose never looks frozen
 */
import { Assets, Container } from 'pixi.js';
import { Spine, type TrackEntry } from '@esotericsoftware/spine-pixi-v8';

export const IDLE_TRACK = 0;
export const REACTION_TRACK = 1;
export const BREATH_TRACK = 2;

/** Measured from the capture: the second fighter answers 0.17 s after the first. */
export const PAIR_STAGGER = 0.17;

export interface FighterFiles {
  /** Asset alias prefix, e.g. 'fighterL' -> 'fighterL-skel' and 'fighterL-atlas'. */
  name: string;
  skel: string;
  atlas: string;
}

/** Loads skeleton + atlas. Export binary .skel from the Spine 4.3 editor (smaller and faster than JSON). */
export async function loadFighters(files: FighterFiles[]): Promise<void> {
  await Assets.load(
    files.flatMap((f) => [
      { alias: `${f.name}-skel`, src: f.skel },
      { alias: `${f.name}-atlas`, src: f.atlas },
    ]),
  );
}

export function createFighter(name: string, scale = 1): Spine {
  // Spine 4.3: construct directly. Spine.from() still works but is deprecated and scheduled for removal.
  const fighter = new Spine({ skeleton: `${name}-skel`, atlas: `${name}-atlas`, scale });
  fighter.state.setAnimation(IDLE_TRACK, 'idle', true);
  return fighter;
}

/** Adds the optional additive breathing layer. Its keys must be offsets from the setup pose, not absolute values. */
export function addBreathLayer(fighter: Spine, animation = 'breath'): TrackEntry {
  const entry = fighter.state.setAnimation(BREATH_TRACK, animation, true);
  entry.additive = true; // must be set before the next AnimationState.apply
  return entry;
}

export interface ReactOptions {
  /** Seconds to wait before the clip starts. */
  delay?: number;
  /** Mix-in time. Short is fine: the clip's first frame is a smear that hides the cut. */
  mixIn?: number;
  /** Mix-out time back to the idle. */
  mixOut?: number;
}

/** Plays a one-shot reaction on track 1, then mixes back to the idle that has been running on track 0 the whole time. */
export function react(fighter: Spine, animation: string, { delay = 0, mixIn = 0.08, mixOut = 0.15 }: ReactOptions = {}): TrackEntry {
  fighter.state.setEmptyAnimation(REACTION_TRACK, 0);
  const entry = fighter.state.addAnimation(REACTION_TRACK, animation, false, delay);
  entry.setMixDuration(mixIn, delay); // sets the mix AND recomputes the delay (4.3 API)
  fighter.state.addEmptyAnimation(REACTION_TRACK, mixOut, 0); // starts mixing out mixOut seconds before the clip ends
  return entry;
}

/** Both fighters punch on the same frame as the reel event; the second one 0.17 s later, like an exchange of blows. */
export function punchExchange(first: Spine, second: Spine, animation = 'punch'): void {
  react(first, animation);
  react(second, animation, { delay: PAIR_STAGGER });
}

/** Calls `onImpact` when the animation reaches its 'impact' event (key it on the frame of full arm extension). */
export function onImpact(fighter: Spine, onImpact: (fighter: Spine) => void, eventName = 'impact'): void {
  fighter.state.addListener({
    event: (_entry, event) => {
      if (event.data.name === eventName) onImpact(fighter);
    },
  });
}

/** Example wiring. `reelEvents` and `fx` stand in for the game's own reel and effects modules. */
export async function setupFighters(
  stage: Container,
  reelEvents: { on(type: 'wildExpand', cb: (reelIndex: number) => void): void },
  fx: { sound(id: string): void; shake(px: number, seconds: number): void },
): Promise<{ left: Spine; right: Spine }> {
  await loadFighters([
    { name: 'fighterL', skel: 'spines/fighterL.skel', atlas: 'spines/fighterL.atlas' },
    { name: 'fighterR', skel: 'spines/fighterR.skel', atlas: 'spines/fighterR.atlas' },
  ]);
  const left = createFighter('fighterL');
  const right = createFighter('fighterR');
  stage.addChild(left, right);

  for (const f of [left, right]) {
    onImpact(f, () => {
      fx.sound('punch');
      fx.shake(6, 0.12);
    });
  }

  // Same frame as the wild reel expanding. Whoever's wild landed throws first.
  reelEvents.on('wildExpand', (reelIndex) => {
    if (reelIndex >= 2) punchExchange(right, left);
    else punchExchange(left, right);
  });
  return { left, right };
}
