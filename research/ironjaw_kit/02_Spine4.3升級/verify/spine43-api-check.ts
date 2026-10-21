/**
 * Compile-time proof for the web-sdk patch, checked against the real @esotericsoftware/spine-pixi-v8 4.3.13 types.
 *  - The patched code (bone.getPose(), slot.getAppliedPose().attachment, new Spine(skeletonData)) must compile.
 *  - Every 4.2-style access the patch removes must FAIL to compile: each is marked @ts-expect-error, so if 4.3
 *    still accepted it, tsc would report "Unused '@ts-expect-error' directive" and this check would fail.
 */
import * as SPINE_PIXI from '@esotericsoftware/spine-pixi-v8';

declare const spine: SPINE_PIXI.Spine;
declare const skeletonData: SPINE_PIXI.SkeletonData;

// ---- BaseSpineProvider.svelte: unchanged, the 4.3 constructor still accepts bare SkeletonData
export const fromData: SPINE_PIXI.Spine = new SPINE_PIXI.Spine(skeletonData);

// ---- SpineBone.svelte (patched)
type BoneProps = Partial<SPINE_PIXI.BonePose> & { boneName: string };
export const boneProps: BoneProps = { boneName: 'root', x: 10, y: 20, rotation: 5, scaleX: 1, scaleY: 1 };
const bone = spine.skeleton.findBone('root');
const pose = bone?.getPose();
if (pose) {
  pose.x = 10;
  pose.y = -20;
  pose.rotation = 5;
}
// 4.2 code wrote straight to the bone. In 4.3 these properties are gone:
// @ts-expect-error Bone has no y in 4.3
if (bone) bone.y = -20;
// @ts-expect-error Bone has no rotation in 4.3
if (bone) bone.rotation = 5;

// ---- SpineSlot.svelte (patched)
const slot = spine.skeleton.findSlot('slot_win_count');
export const showPatched: boolean = Boolean(slot?.getAppliedPose().attachment);
// 4.2 code read the attachment off the slot. In 4.3 it is gone (at runtime it would be undefined -> the slot content hides):
// @ts-expect-error Slot has no attachment in 4.3
export const show42: boolean = Boolean(slot?.attachment);

// ---- SpineTrack.svelte: TrackEntry props the example games pass still exist; removed ones are rejected
type TrackProps = Partial<SPINE_PIXI.TrackEntry>;
export const okTrackProps: TrackProps = { loop: true, mixDuration: 0.2, timeScale: 1, additive: false };
// @ts-expect-error TrackEntry.mixBlend was removed in 4.3 (replaced by additive)
export const removed1: TrackProps = { mixBlend: 1 };
// @ts-expect-error TrackEntry.holdPrevious was removed in 4.3
export const removed2: TrackProps = { holdPrevious: true };

// ---- SpineEventEmitterProvider.svelte: hooks still exist
spine.beforeUpdateWorldTransforms = () => {};
spine.afterUpdateWorldTransforms = () => {};

// ---- assetLoad.ts: loader API unchanged
declare const atlas: SPINE_PIXI.TextureAtlas;
const binary = new SPINE_PIXI.SkeletonBinary(new SPINE_PIXI.AtlasAttachmentLoader(atlas));
binary.scale = 0.5;
export const parsed: SPINE_PIXI.SkeletonData = binary.readSkeletonData(new Uint8Array(0));
export const size: number = parsed.width + parsed.height;

// ---- Renamed in 4.3 (only matters if your own code calls them)
spine.skeleton.setupPose();
// @ts-expect-error setToSetupPose() was renamed to setupPose() in 4.3
spine.skeleton.setToSetupPose();
export const track0 = spine.state.getTrack(0);
