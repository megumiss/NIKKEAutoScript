<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'

export type Point = [number, number]
export type Enemy = { kind: 'normal' | 'ex'; position: Point; confidence: number }
export type RememberedEnemy = { id: string; kind: 'normal' | 'ex' | null; position: Point; status: 'pending' | 'engaged' | 'unreachable' }
export type Block = { id: string; kind?: string; point: Point; segment: [Point, Point]; status?: 'active' | 'stale' }
export type Goal = { kind: string; id: string; position: Point }
export type AnnotatedPoint = { id: string; label: string; category: 'ground_mechanism' | 'ground_elevator'; position: Point }
export type ElevatorTransfer = { id: string; entry: Point; exit: Point; directed: boolean }
export type MapAnnotations = { points: AnnotatedPoint[]; transfers: ElevatorTransfer[] }
const props = withDefaults(defineProps<{
  image: string; size: Point; squad?: Point | null; enemies: Enemy[]
  remembered?: RememberedEnemy[]
  blocks?: Block[]
  goal?: Goal | null
  annotations?: MapAnnotations | null
  label: string
}>(), { remembered: () => [], blocks: () => [], goal: null, annotations: null })
const emit = defineEmits<{ error: [] }>()
const blockWidth = computed(() => props.size[0] * .006)
const marker = computed(() => props.size[0] * .012)
const statusLabels: Record<RememberedEnemy['status'], string> = { pending: '待处理', engaged: '已交战', unreachable: '暂时走不到' }
function diamond(radius: number) { return `M 0 ${-radius} L ${radius} 0 L 0 ${radius} L ${-radius} 0 Z` }
/** 电梯连线方向取自底图端点，不依赖当前小队定位；返回 SVG 旋转所需的角度。 */
function transferAngle(transfer: ElevatorTransfer) {
  return Math.atan2(transfer.exit[1] - transfer.entry[1], transfer.exit[0] - transfer.entry[0]) * 180 / Math.PI
}
</script>

<template>
  <svg class="campaign-map" :viewBox="`0 0 ${size[0]} ${size[1]}`" :aria-label="label" role="img">
    <image :href="image" :width="size[0]" :height="size[1]" @error="emit('error')" />
    <g v-for="transfer in annotations?.transfers ?? []" :key="transfer.id" class="elevator-transfer">
      <line :x1="transfer.entry[0]" :y1="transfer.entry[1]" :x2="transfer.exit[0]" :y2="transfer.exit[1]" vector-effect="non-scaling-stroke" />
      <g :transform="`translate(${(transfer.entry[0] + transfer.exit[0]) / 2} ${(transfer.entry[1] + transfer.exit[1]) / 2}) rotate(${transferAngle(transfer)})`">
        <path :d="`M ${-marker * .6} ${-marker * .6} L ${marker * .6} 0 L ${-marker * .6} ${marker * .6}`" vector-effect="non-scaling-stroke" />
        <path v-if="!transfer.directed" :d="`M ${-marker * 1.2} ${-marker * .6} L ${-marker * 2.4} 0 L ${-marker * 1.2} ${marker * .6}`" vector-effect="non-scaling-stroke" />
      </g>
      <title>{{ t('电梯连接') }} {{ transfer.id }} · {{ transfer.directed ? t('单向') : t('双向') }} · {{ transfer.entry.join(', ') }} {{ transfer.directed ? '→' : '↔' }} {{ transfer.exit.join(', ') }}</title>
    </g>
    <g v-for="point in annotations?.points ?? []" :key="point.id" :transform="`translate(${point.position.join(' ')})`" :class="['annotation', point.category]">
      <rect :x="-marker" :y="-marker" :width="marker * 2" :height="marker * 2" :rx="point.category === 'ground_elevator' ? marker * .5 : 0" vector-effect="non-scaling-stroke" />
      <path v-if="point.category === 'ground_elevator'" :d="`M ${-marker * .5} ${-marker * .15} L 0 ${-marker * .6} L ${marker * .5} ${-marker * .15} M ${-marker * .5} ${marker * .15} L 0 ${marker * .6} L ${marker * .5} ${marker * .15}`" vector-effect="non-scaling-stroke" />
      <circle v-else :r="marker * .3" />
      <title>{{ t('已标注') }} · {{ point.category === 'ground_mechanism' ? t('地面机关') : t('电梯') }} · {{ point.label || point.id }} · {{ point.position.join(', ') }}</title>
    </g>
    <g v-for="block in blocks" :key="block.id" :class="['block', block.status]">
      <line :x1="block.segment[0][0]" :y1="block.segment[0][1]" :x2="block.segment[1][0]" :y2="block.segment[1][1]" :stroke-width="blockWidth" :stroke-dasharray="`${blockWidth * 1.6} ${blockWidth}`" />
      <title>{{ t('道路封锁') }} · {{ block.point.join(', ') }}</title>
    </g>
    <g v-for="enemy in remembered" :key="enemy.id" :transform="`translate(${enemy.position.join(' ')})`" :class="['remembered', enemy.kind, enemy.status]">
      <path :d="diamond(marker * 1.35)" vector-effect="non-scaling-stroke" />
      <title>{{ t('记住的敌人') }} {{ enemy.id }} · {{ enemy.kind === 'ex' ? 'EX' : t('敌人') }} · {{ t(statusLabels[enemy.status]) }} · {{ enemy.position.join(', ') }}</title>
    </g>
    <g v-for="(enemy, i) in enemies" :key="i" :transform="`translate(${enemy.position.join(' ')})`" :class="['enemy', enemy.kind]">
      <path :d="diamond(marker)" vector-effect="non-scaling-stroke" />
      <title>{{ enemy.kind === 'ex' ? 'EX' : t('敌人') }} · {{ enemy.position.join(', ') }}</title>
    </g>
    <g v-if="goal" class="goal" :transform="`translate(${goal.position.join(' ')})`">
      <circle :r="size[0] * .02" vector-effect="non-scaling-stroke" />
      <path :d="`M ${-size[0] * .03} 0 H ${size[0] * .03} M 0 ${-size[0] * .03} V ${size[0] * .03}`" vector-effect="non-scaling-stroke" />
      <title>{{ t('当前目标') }} · {{ goal.kind }} {{ goal.id }} · {{ goal.position.join(', ') }}</title>
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
.remembered { fill:none; stroke:#e95e7d; stroke-width:2px; }
.remembered.ex { stroke:#c998ff; }
.remembered.engaged { opacity:.45; }
.remembered.unreachable { stroke:#9aa4b2; stroke-dasharray:4 3; }
.goal { fill:none; stroke:#ffd166; stroke-width:2.5px; }
.block line { stroke:#ff5b5b; stroke-linecap:round; opacity:.9; }
.block.stale line { opacity:.45; }
.annotation { fill:var(--card); stroke-width:2px; }
.annotation.ground_mechanism { stroke:var(--green); }.annotation.ground_mechanism circle { fill:var(--green); stroke:none; }
.annotation.ground_elevator,.elevator-transfer { stroke:var(--yellow); }
.annotation path,.elevator-transfer { fill:none; }
.elevator-transfer { stroke-width:1.5px; }.elevator-transfer line { stroke-dasharray:5 4; }
</style>
