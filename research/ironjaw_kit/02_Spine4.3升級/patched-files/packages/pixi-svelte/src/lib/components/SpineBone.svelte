<script lang="ts" module>
	import * as SPINE_PIXI from '@esotericsoftware/spine-pixi-v8';

	// Spine 4.3: a bone's local transform (x, y, rotation, scaleX, ...) lives on its pose, not on Bone
	export type Props = Partial<SPINE_PIXI.BonePose> & {
		boneName: Parameters<SPINE_PIXI.Spine['skeleton']['findBone']>[0];
	};
</script>

<script lang="ts">
	import { propsSyncEffect } from '../utils.svelte';
	import { getContextSpine } from '../context.svelte';

	const props: Props = $props();
	const spine = getContextSpine();
	const bone = spine.skeleton.findBone(props.boneName);
	const pose = bone?.getPose();

	propsSyncEffect({ props, target: pose, ignore: ['boneName', 'y'] });
	$effect(() => {
		if (pose && props.y !== undefined) pose.y = -props.y;
	});
</script>
