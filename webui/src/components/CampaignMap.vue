<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import CampaignMarker from './CampaignMarker.vue'

export type Point = [number, number]
export type Enemy = { kind: 'normal' | 'ex' | 'boss'; position: Point; confidence: number }
export type RememberedEnemy = { id: string; kind: 'normal' | 'ex' | 'boss' | null; position: Point; status: 'pending' | 'engaged' | 'unreachable'; confirmed?: boolean; visible?: boolean }
export type Block = { id: string; kind?: string; point: Point; segment: [Point, Point]; status?: 'active' | 'stale' }
export type RoutePath = { target_id: string; points: Point[]; distance: number }
export type PlannedRoutes = { execution?: RoutePath; destination?: RoutePath }
export type Goal = { kind: string; id: string; position: Point; reason_text?: string; enemy_kind?: 'normal' | 'boss' | null }
export type AnnotatedPoint = { id: string; label: string; category: 'ground_mechanism' | 'ground_elevator'; position: Point }
export type ElevatorTransfer = { id: string; entry: Point; exit: Point; directed: boolean }
export type MechanismState = 'on' | 'off' | 'unknown'
export type MapAnnotations = { points: AnnotatedPoint[]; transfers: ElevatorTransfer[] }
const props = withDefaults(defineProps<{
  image: string; size: Point; squad?: Point | null; enemies: Enemy[]
  remembered?: RememberedEnemy[]
  confirmationAware?: boolean
  blocks?: Block[]
  goal?: Goal | null
  routeGoal?: Goal | null
  routes?: PlannedRoutes
  annotations?: MapAnnotations | null
  mechanismStates?: Record<string, MechanismState>
  label: string
}>(), { remembered: () => [], blocks: () => [], goal: null, routeGoal: null, routes: () => ({}), annotations: null, mechanismStates: () => ({}) })
/** 仅显示与当前目标绑定且坐标有效的道路折线；换目标或无路径时不保留旧线。 */
const routeLines = computed(() => (['destination', 'execution'] as const).flatMap(kind => {
  const route = props.routes[kind]
  const target = kind === 'execution' ? props.goal : props.routeGoal
  if (!route || !target || route.target_id !== target.id || route.points.length < 2
      || !route.points.every(point => point.length === 2 && point.every(Number.isFinite))) return []
  return [{ kind, points: route.points.map(point => point.join(',')).join(' '), distance: route.distance }]
}))
const emit = defineEmits<{ error: [] }>()
const marker = computed(() => props.size[0] * .012)
const statusLabels: Record<RememberedEnemy['status'], string> = { pending: '待处理', engaged: '已交战', unreachable: '暂时走不到' }
/** 新快照按后端确认字段展示，不再把历史位置与当前检测猜测关联；旧快照仍兼容原展示。 */
const enemyMarkers = computed(() => {
  if (props.confirmationAware || props.remembered.some(enemy => enemy.confirmed !== undefined)) {
    return props.remembered.filter(enemy => enemy.confirmed).map(enemy => ({
      ...enemy, visible: enemy.visible ?? false, confidence: null as number | null,
    }))
  }
  const markers = props.remembered.map(enemy => ({ ...enemy, visible: false, confidence: null as number | null }))
  const matched = new Set<number>()
  props.enemies.forEach((enemy, index) => {
    let nearest = -1
    let distance = marker.value * 1.6
    markers.forEach((memory, candidate) => {
      if (candidate >= props.remembered.length || matched.has(candidate) || (memory.kind ?? 'normal') !== enemy.kind) return
      const span = Math.hypot(memory.position[0] - enemy.position[0], memory.position[1] - enemy.position[1])
      if (span <= distance) { nearest = candidate; distance = span }
    })
    if (nearest >= 0) {
      matched.add(nearest)
      markers[nearest] = { ...markers[nearest], position: enemy.position, visible: true, confidence: enemy.confidence }
    } else {
      markers.push({ ...enemy, id: `visible-${index}`, status: 'pending', visible: true })
    }
  })
  return markers
})
/** 历史位置仅用线索圆点展示，不与已确认敌人共享三角形标记。 */
const enemyHints = computed(() => props.remembered.filter(enemy => enemy.confirmed === false))
/** 已完成的机关状态由调用方归一为 on；没有状态证据时明确显示未知，不使用绿色已开启外观。 */
function mechanismState(point: AnnotatedPoint) { return props.mechanismStates[point.id] ?? 'unknown' }
const mechanismLabels: Record<MechanismState, string> = { on: '已开启', off: '未开启', unknown: '未检测到状态' }
/** 展示实际敌人类型；Boss 和 EX 不映射成普通敌人。 */
function enemyLabel(kind: Enemy['kind'] | null) {
  return kind === 'boss' ? 'Boss' : kind === 'ex' ? 'EX' : t('敌人')
}
/** 电梯连线方向取自底图端点，不依赖当前小队定位；返回 SVG 旋转所需的角度。 */
function transferAngle(transfer: ElevatorTransfer) {
  return Math.atan2(transfer.exit[1] - transfer.entry[1], transfer.exit[0] - transfer.entry[0]) * 180 / Math.PI
}
</script>

<template>
  <svg class="campaign-map" :viewBox="`0 0 ${size[0]} ${size[1]}`" :aria-label="label" role="img">
    <image :href="image" :width="size[0]" :height="size[1]" @error="emit('error')" />
    <g v-for="route in routeLines" :key="route.kind" class="planned-route" :class="route.kind">
      <polyline class="route-outline" :points="route.points" vector-effect="non-scaling-stroke" />
      <polyline :points="route.points" vector-effect="non-scaling-stroke" />
      <title>{{ t(route.kind === 'execution' ? '本段路线' : '目的地路线') }} · {{ route.distance.toFixed(1) }} {{ t('地图像素') }}</title>
    </g>
    <g v-for="transfer in annotations?.transfers ?? []" :key="transfer.id" class="elevator-transfer">
      <line :x1="transfer.entry[0]" :y1="transfer.entry[1]" :x2="transfer.exit[0]" :y2="transfer.exit[1]" vector-effect="non-scaling-stroke" />
      <g :transform="`translate(${(transfer.entry[0] + transfer.exit[0]) / 2} ${(transfer.entry[1] + transfer.exit[1]) / 2}) rotate(${transferAngle(transfer)})`">
        <path :d="`M ${-marker * .6} ${-marker * .6} L ${marker * .6} 0 L ${-marker * .6} ${marker * .6}`" vector-effect="non-scaling-stroke" />
        <path v-if="!transfer.directed" :d="`M ${-marker * 1.2} ${-marker * .6} L ${-marker * 2.4} 0 L ${-marker * 1.2} ${marker * .6}`" vector-effect="non-scaling-stroke" />
      </g>
      <title>{{ t('电梯连接') }} {{ transfer.id }} · {{ transfer.directed ? t('单向') : t('双向') }} · {{ transfer.entry.join(', ') }} {{ transfer.directed ? '→' : '↔' }} {{ transfer.exit.join(', ') }}</title>
    </g>
    <g v-for="point in annotations?.points ?? []" :key="point.id" :transform="`translate(${point.position.join(' ')})`" :class="['annotation', point.category, mechanismState(point)]">
      <CampaignMarker :kind="point.category === 'ground_mechanism' ? 'mechanism' : 'elevator'" :state="mechanismState(point)" :size="marker * 2.8" :x="-marker * 1.4" :y="-marker * 1.4" />
      <title>{{ t('已标注') }} · {{ point.category === 'ground_mechanism' ? t('地面机关') : t('电梯') }} · {{ point.label || point.id }}<template v-if="point.category === 'ground_mechanism'"> · {{ t(mechanismLabels[mechanismState(point)]) }}</template> · {{ point.position.join(', ') }}</title>
    </g>
    <g v-for="block in blocks" :key="block.id" :class="['block', block.status]">
      <line class="block-outline" :x1="block.segment[0][0]" :y1="block.segment[0][1]" :x2="block.segment[1][0]" :y2="block.segment[1][1]" vector-effect="non-scaling-stroke" />
      <line :x1="block.segment[0][0]" :y1="block.segment[0][1]" :x2="block.segment[1][0]" :y2="block.segment[1][1]" vector-effect="non-scaling-stroke" />
      <rect v-for="(end, index) in block.segment" :key="index" :x="end[0] - marker * .35" :y="end[1] - marker * .35" :width="marker * .7" :height="marker * .7" :rx="marker * .1" vector-effect="non-scaling-stroke" />
      <title>{{ t('道路封锁') }} · {{ block.point.join(', ') }}<template v-if="block.status === 'stale'"> · {{ t('待复核') }}</template></title>
    </g>
    <g v-for="enemy in enemyMarkers" :key="enemy.id" :transform="`translate(${enemy.position.join(' ')})`" :class="['enemy-marker', enemy.kind, enemy.status, { visible: enemy.visible }]">
      <CampaignMarker :kind="enemy.kind === 'boss' ? 'boss' : enemy.kind === 'ex' ? 'ex' : 'enemy'" :state="enemy.status === 'pending' ? enemy.visible ? 'visible' : 'remembered' : enemy.status" :size="marker * (enemy.kind === 'boss' ? 3.3 : 2.6)" :x="-marker * (enemy.kind === 'boss' ? 1.65 : 1.3)" :y="-marker * (enemy.kind === 'boss' ? 1.65 : 1.3)" />
      <title>{{ enemy.visible ? t('当前可见') : t('本轮确认的敌人') }} · {{ enemyLabel(enemy.kind) }} · {{ enemy.id }} · {{ t(statusLabels[enemy.status]) }} · {{ enemy.position.join(', ') }}</title>
    </g>
    <g v-for="hint in enemyHints" :key="`hint-${hint.id}`" :transform="`translate(${hint.position.join(' ')})`" class="enemy-hint">
      <CampaignMarker kind="hint" :size="marker * 1.4" :x="-marker * .7" :y="-marker * .7" />
      <title>{{ t('历史敌人线索') }} · {{ enemyLabel(hint.kind) }} · {{ hint.position.join(', ') }} · {{ t('需重新观察，不直接执行') }}</title>
    </g>
    <g v-if="routeGoal" class="route-goal" :transform="`translate(${routeGoal.position.join(' ')})`">
      <CampaignMarker kind="destination" :size="marker * 4.2" :x="-marker * 2.1" :y="-marker * 2.1" />
      <title>{{ t('路程目标') }} · {{ routeGoal.id }} · {{ routeGoal.position.join(', ') }}</title>
    </g>
    <g v-if="goal" class="goal" :transform="`translate(${goal.position.join(' ')})`">
      <CampaignMarker kind="execution" :size="marker * 3.4" :x="-marker * 1.7" :y="-marker * 1.7" />
      <title>{{ t('执行目标') }} · {{ goal.enemy_kind === 'boss' ? 'Boss' : goal.kind }} {{ goal.id }} · {{ goal.position.join(', ') }}</title>
    </g>
    <g v-if="squad" class="squad" :transform="`translate(${squad.join(' ')})`">
      <CampaignMarker kind="squad" :size="marker * 3.4" :x="-marker * 1.7" :y="-marker * 1.7" />
      <title>{{ t('小队位置') }} · {{ squad.join(', ') }}</title>
    </g>
  </svg>
</template>

<style scoped>
.campaign-map { display:block; width:100%; max-height:520px; background:#101b27; }
.planned-route { fill:none; stroke-linejoin:round; stroke-linecap:round; pointer-events:none; }
.planned-route polyline { stroke:#ffd166; stroke-width:3px; }
.planned-route.destination polyline { stroke:#c998ff; stroke-width:2px; stroke-dasharray:6 5; }
.planned-route polyline.route-outline { stroke:#101b27; stroke-width:6px; opacity:.85; }
.planned-route.destination polyline.route-outline { stroke-width:5px; }
.block line { stroke:#ff718e; stroke-width:4px; stroke-linecap:round; }
.block line.block-outline { stroke:#101b27; stroke-width:7px; }
.block rect { fill:#1e2c39; stroke:#ff718e; stroke-width:1.5px; }
.block.stale line:not(.block-outline) { stroke:#a6b5c4; stroke-dasharray:5 4; }
.block.stale rect { stroke:#a6b5c4; }
.elevator-transfer { fill:none; stroke:#64e5ff; }
.elevator-transfer { stroke-width:1.5px; }.elevator-transfer line { stroke-dasharray:5 4; }
</style>
