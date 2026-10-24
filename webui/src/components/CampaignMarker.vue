<script setup lang="ts">
export type MarkerKind = 'enemy' | 'boss' | 'ex' | 'mechanism' | 'elevator' | 'execution' | 'destination' | 'squad' | 'barrier'
withDefaults(defineProps<{ kind: MarkerKind; state?: string; size?: number }>(), { state: 'visible', size: 22 })
</script>

<template>
  <svg class="campaign-marker" :class="[kind, state]" :width="size" :height="size" viewBox="-16 -16 32 32" aria-hidden="true">
    <template v-if="kind === 'enemy' || kind === 'boss' || kind === 'ex'">
      <path v-if="kind === 'enemy'" class="body-fill" d="M 0 11 L -11 -8 H 11 Z" />
      <path v-else class="body-line" :d="kind === 'boss' ? 'M 0 13 L -13 -9 H 13 Z' : 'M 0 12 L -12 -8 H 12 Z'" />
      <template v-if="kind === 'ex'">
        <path class="body-fill" d="M -1.7 -4 H 1.7 L 1.1 3 H -1.1 Z" /><circle class="body-fill" cy="7" r="1.8" />
      </template>
      <template v-else-if="kind === 'boss'">
        <path class="body-fill" d="M -6 -2.5 L -3.6 -1.6 A 4.3 4.3 0 1 1 3.6 -1.6 L 6 -2.5 L 4 0.8 L 3 0.8 L 3 4 L 1.8 4 L 1.8 3 L 0.6 3 L 0.6 4 L -0.6 4 L -0.6 3 L -1.8 3 L -1.8 4 L -3 4 L -3 0.8 L -4 0.8 Z" />
        <circle class="body-hole" cx="-1.8" cy="0.5" r="1.1" /><circle class="body-hole" cx="1.8" cy="0.5" r="1.1" />
      </template>
      <template v-if="state === 'engaged' || state === 'unreachable'">
        <circle class="status-badge" cx="10" cy="10" r="6" />
        <path v-if="state === 'engaged'" class="status-glyph" d="M 7 10 L 9 12 L 13 8" />
        <path v-else class="status-glyph" d="M 8 8 L 12 12 M 12 8 L 8 12" />
      </template>
    </template>
    <template v-else-if="kind === 'mechanism'">
      <path class="body-line" d="M -6 -13 H 6 L 13 -6 V 6 L 6 13 H -6 L -13 6 V -6 Z" />
      <path v-if="state === 'on'" class="body-line" d="M -6 0 L -2 5 L 7 -5" />
      <path v-else-if="state === 'off'" class="body-line" d="M 0 -8 V -1 M -4 -5 A 7 7 0 1 0 4 -5" />
      <g v-else><path class="body-line" d="M -4 -4 A 4 4 0 1 1 2 0 Q 0 1 0 4" /><circle class="body-fill" cy="8" r="1" /></g>
    </template>
    <template v-else-if="kind === 'elevator'">
      <path class="body-line" d="M -12 -5 L 0 -13 L 12 -5 V 5 L 0 13 L -12 5 Z" />
      <path class="body-line" d="M -4 7 V -7 M -8 -3 L -4 -7 L 0 -3 M 4 -7 V 7 M 0 3 L 4 7 L 8 3" />
    </template>
    <circle v-else-if="kind === 'execution'" class="target-ring" r="12" />
    <template v-else-if="kind === 'destination'">
      <path class="body-fill" d="M -5 12 V -12 L 12 -7 L -5 -2 Z" /><circle class="body-fill" cx="-5" cy="12" r="2" />
    </template>
    <path v-else-if="kind === 'squad'" class="body-fill" d="M 0 -11 L 9 9 L 0 4.5 L -9 9 Z" />
    <template v-else>
      <path class="barrier-line" d="M -11 0 H 11" />
      <rect class="body-fill" x="-14" y="-3.5" width="5" height="7" rx="1" /><rect class="body-fill" x="9" y="-3.5" width="5" height="7" rx="1" />
    </template>
  </svg>
</template>

<style scoped>
.campaign-marker { display:inline-block; flex:none; vertical-align:middle; overflow:visible; color:#ff718e; filter:drop-shadow(0 0 1.2px rgba(8,14,20,.85)); }
.campaign-marker.destination { color:#c998ff; }
.campaign-marker.mechanism { color:#a6b5c4; }.campaign-marker.mechanism.off { color:#ffd166; }
.campaign-marker.mechanism.on { color:#64df9b; }.campaign-marker.elevator { color:#64e5ff; }
.campaign-marker.squad { color:#eaf6ff; }.campaign-marker.execution { color:#ffd166; }
.body-fill { fill:currentColor; stroke:none; }
.body-hole { fill:#101b27; stroke:none; }
.body-line,.status-glyph,.target-ring { fill:none; stroke:currentColor; stroke-width:1.8px; stroke-linejoin:round; stroke-linecap:round; vector-effect:non-scaling-stroke; }
.target-ring { stroke-width:2.2px; }
.barrier-line { fill:none; stroke:currentColor; stroke-width:3.5px; stroke-linecap:round; vector-effect:non-scaling-stroke; }
.status-badge { fill:#1e2c39; stroke:currentColor; stroke-width:1px; vector-effect:non-scaling-stroke; }
.status-glyph { stroke-width:1.3px; }
/* 记住的敌人用空心呈现且不减淡：填充转描边，骷髅的眼洞（镂空色）随之隐藏 */
.remembered .body-fill { fill:none; stroke:currentColor; stroke-width:1.6px; stroke-linejoin:round; vector-effect:non-scaling-stroke; }
.remembered .body-hole { visibility:hidden; }
.engaged { opacity:.55; }
.unreachable { color:#a6b5c4; }
</style>
