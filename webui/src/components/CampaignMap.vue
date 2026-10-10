<script setup lang="ts">
import { t } from '../i18n'

export type Point = [number, number]
export type Enemy = { kind: 'normal' | 'ex'; position: Point; confidence: number }
defineProps<{
  image: string; size: Point; squad?: Point | null; enemies: Enemy[]
  label: string
}>()
const emit = defineEmits<{ error: [] }>()
</script>

<template>
  <svg class="campaign-map" :viewBox="`0 0 ${size[0]} ${size[1]}`" :aria-label="label" role="img">
    <image :href="image" :width="size[0]" :height="size[1]" @error="emit('error')" />
    <g v-for="(enemy, i) in enemies" :key="i" :transform="`translate(${enemy.position.join(' ')})`" :class="['enemy', enemy.kind]">
      <path :d="`M 0 ${-size[0] * .012} L ${size[0] * .012} 0 L 0 ${size[0] * .012} L ${-size[0] * .012} 0 Z`" vector-effect="non-scaling-stroke" />
      <title>{{ enemy.kind === 'ex' ? 'EX' : t('敌人') }} · {{ enemy.position.join(', ') }}</title>
    </g>
    <g v-if="squad" class="squad" :transform="`translate(${squad.join(' ')})`">
      <circle :r="size[0] * .017" vector-effect="non-scaling-stroke" />
      <circle :r="size[0] * .004" class="center" />
      <title>{{ t('小队位置') }} · {{ squad.join(', ') }}</title>
    </g>
  </svg>
</template>

<style scoped>
.campaign-map { display:block; width:100%; max-height:520px; background:#101b27; }
.squad { fill:none; stroke:#64e5ff; stroke-width:2.5px; }
.squad .center { fill:#64e5ff; stroke:none; }
.enemy { fill:#e95e7d; stroke:#fff; stroke-width:1px; }
.enemy.ex { fill:#c998ff; }
</style>
