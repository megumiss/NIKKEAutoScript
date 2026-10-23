<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api/client'
import { t, uiLanguage } from '../i18n'
import { useInstancesStore } from '../stores/instances'
import CampaignMarker from './CampaignMarker.vue'
import CampaignMap, { type Block, type Enemy, type Goal, type MapAnnotations, type MechanismState, type Point, type PlannedRoutes, type RememberedEnemy } from './CampaignMap.vue'

const props = defineProps<{ name: string }>()
type Snapshot = {
  chapter: number; difficulty: 'normal' | 'hard'; captured_at: number
  map: { size: Point; image_sha256: string } | null
  minimap: { image: string; size: Point; squad: Point | null; enemies: Enemy[]; mode: string } | null
  localization: string; squad: Point | null; enemies: Enemy[]
  barriers?: { state: string; map: Point }[]; blocks?: Block[]
  annotations?: MapAnnotations | null
  memory?: { tick: number; enemies: RememberedEnemy[]; blocks: Block[]; goal?: Goal; route_goal?: Goal | null; routes?: PlannedRoutes;
    mechanisms?: { id: string; state: string; completed: boolean }[] }
}
const instances = useInstancesStore()
const snapshot = ref<Snapshot | null>(null)
const running = ref(false)
const error = ref('')
const mapError = ref(false)
let generation = 0
const mapUrl = computed(() => snapshot.value?.map ? `/api/${encodeURIComponent(props.name)}/campaign/map/${snapshot.value.chapter}?v=${snapshot.value.map.image_sha256}` : '')
const updatedAt = computed(() => snapshot.value ? new Date(snapshot.value.captured_at * 1000).toLocaleTimeString(uiLanguage.value) : '')
// 本帧快照只含视野内的敌人；远处敌人与离开视野的封锁要从任务推送的运行记忆里取，没有记忆时退回本帧。
const remembered = computed(() => snapshot.value?.memory?.enemies ?? [])
const mapBlocks = computed(() => snapshot.value?.memory?.blocks ?? snapshot.value?.blocks ?? [])
const goal = computed(() => snapshot.value?.memory?.goal ?? null)
const routes = computed(() => snapshot.value?.memory?.routes ?? {})
const routeGoal = computed(() => snapshot.value?.memory?.route_goal ?? null)
const mechanismStates = computed<Record<string, MechanismState>>(() => Object.fromEntries(
  (snapshot.value?.memory?.mechanisms ?? []).map(item => [item.id, item.completed || item.state === 'on' ? 'on' : item.state === 'off' ? 'off' : 'unknown']),
))
const goalKinds: Record<string, string> = { enemy: '敌人', story: '主线目标', mechanism: '地面机关',
  advance: '沿路推进', explore: '未探索区域', elevator: '电梯', review_block: '复核光幕', collectible: '收集品' }
const annotations = computed(() => snapshot.value?.annotations ?? null)
const mechanisms = computed(() => annotations.value?.points.filter(point => point.category === 'ground_mechanism') ?? [])
const elevators = computed(() => annotations.value?.points.filter(point => point.category === 'ground_elevator') ?? [])
function format(point?: Point | null) { return point ? point.map(value => value.toFixed(1)).join(', ') : '—' }
watch(mapUrl, () => { mapError.value = false })
watch(() => props.name, () => { snapshot.value = null; error.value = ''; running.value = false })
watch([
  () => props.name,
  () => instances.campaignRevisions[props.name],
  () => instances.instances.find(item => item.name === props.name)?.state,
], async () => {
  const version = ++generation
  if (!props.name) return
  try {
    const result = await api.get(`/api/${encodeURIComponent(props.name)}/campaign`)
    if (version !== generation) return
    snapshot.value = result.snapshot
    running.value = result.running
    error.value = ''
  } catch (reason) {
    if (version === generation) error.value = String(reason)
  }
}, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <article class="card campaign-monitor">
    <header class="campaign-head">
      <div><h3>{{ t('推图信息') }}</h3><span class="sub">{{ t('当前章节') }} <b>{{ snapshot?.chapter ?? '—' }}</b></span></div>
      <span v-if="snapshot?.difficulty" class="difficulty" :class="snapshot.difficulty">{{ snapshot.difficulty === 'hard' ? t('困难') : t('普通') }} · {{ snapshot.difficulty.toUpperCase() }}</span>
      <div class="freshness"><span>{{ running ? t('运行中') : t('已停止') }}</span><small v-if="updatedAt">{{ t('更新于') }} {{ updatedAt }}</small></div>
    </header>
    <div v-if="!snapshot?.chapter" class="campaign-empty">{{ t('推图任务启动后显示章节信息。') }}</div>
    <div v-else class="campaign-content">
      <section class="chapter-map-section">
        <div class="section-heading"><b>{{ t('章节地图') }}</b><span>{{ mapBlocks.length ? `${mapBlocks.length} ${t('处道路封锁')}` : t('普通 / 困难共用底图') }}</span></div>
        <CampaignMap v-if="snapshot.map && !mapError" :image="mapUrl" :size="snapshot.map.size" :squad="snapshot.squad" :enemies="snapshot.enemies" :remembered="remembered" :blocks="mapBlocks" :goal="goal" :route-goal="routeGoal" :routes="routes" :annotations="annotations" :mechanism-states="mechanismStates" :label="t('章节地图')" @error="mapError = true" />
        <div v-else class="campaign-empty">{{ t('章节底图不可用') }}</div>
        <div v-if="snapshot.memory" class="target-details">
          <div v-for="entry in [{ title: '路程目标', value: routeGoal }, { title: '执行目标', value: goal }]" :key="entry.title" class="target-row">
            <b>{{ t(entry.title) }}</b>
            <template v-if="entry.value"><span>{{ entry.value.enemy_kind === 'boss' ? 'Boss' : t(goalKinds[entry.value.kind] || entry.value.kind) }} · {{ entry.value.id }} · {{ format(entry.value.position) }}</span><small v-if="entry.value.reason_text">{{ t(entry.value.reason_text) }}</small></template>
            <span v-else>—</span>
          </div>
        </div>
        <div class="map-legend">
          <div class="legend marker-legend">
            <span><CampaignMarker kind="squad" :size="16" />{{ t('小队') }}</span>
            <span><CampaignMarker kind="enemy" :size="16" />{{ t('敌人') }}</span>
            <span><CampaignMarker kind="ex" :size="16" />EX</span>
            <span><CampaignMarker kind="boss" :size="16" />Boss</span>
            <span v-if="remembered.length"><CampaignMarker kind="enemy" state="remembered" :size="16" />{{ t('记住的敌人') }} {{ remembered.length }}</span>
            <span v-if="goal"><CampaignMarker kind="execution" :size="16" />{{ t('执行目标') }}</span>
            <span v-if="routeGoal"><CampaignMarker kind="destination" :size="16" />{{ t('路程目标') }}</span>
            <span v-if="mapBlocks.length"><CampaignMarker kind="barrier" :size="16" />{{ t('道路封锁') }}</span>
          </div>
          <div v-if="routes.execution || routes.destination" class="legend route-legend">
            <span v-if="routes.execution" class="gold">━ {{ t('本段路线') }} · {{ routes.execution.distance.toFixed(1) }} {{ t('地图像素') }}</span>
            <span v-if="routes.destination" class="purple">┄ {{ t('目的地路线') }} · {{ routes.destination.distance.toFixed(1) }} {{ t('地图像素') }}</span>
          </div>
          <div v-if="annotations" class="legend annotation-legend marker-legend">
            <span v-if="mechanisms.length"><CampaignMarker kind="mechanism" state="off" :size="16" />{{ t('未开启') }}</span>
            <span v-if="mechanisms.length"><CampaignMarker kind="mechanism" state="on" :size="16" />{{ t('已开启') }}</span>
            <span v-if="mechanisms.length"><CampaignMarker kind="mechanism" state="unknown" :size="16" />{{ t('未检测到状态') }}</span>
            <span v-if="elevators.length"><CampaignMarker kind="elevator" :size="16" />{{ t('电梯') }} {{ elevators.length }}</span>
            <span v-if="annotations.transfers.length"><span class="elevator" aria-hidden="true">⇢</span>{{ t('电梯连接') }} {{ annotations.transfers.length }}</span>
          </div>
          <p class="map-note">{{ snapshot.localization === 'accepted' ? t('位置已匹配到章节底图') : t('底图位置未确认，请查看小地图。') }}</p>
        </div>
      </section>
      <section class="observations">
        <div class="section-heading"><b>{{ t('小地图') }}</b><span>{{ snapshot.minimap?.enemies.length ?? '—' }} {{ t('个可见敌人') }}</span></div>
        <CampaignMap v-if="snapshot.minimap" class="minimap" :image="snapshot.minimap.image" :size="snapshot.minimap.size" :squad="snapshot.minimap.squad" :enemies="snapshot.minimap.enemies" :label="t('小地图')" />
        <div v-else class="campaign-empty compact">{{ t('未检测到小地图') }}</div>
        <dl class="squad-positions">
          <div><dt>{{ t('小队位置') }} · {{ t('底图') }}</dt><dd>{{ format(snapshot.squad) }}</dd></div>
          <div><dt>{{ t('小队位置') }} · {{ t('小地图') }}</dt><dd>{{ format(snapshot.minimap?.squad) }}</dd></div>
        </dl>
        <div v-if="snapshot.minimap?.enemies.length" class="enemy-list">
          <div v-for="(enemy, i) in snapshot.minimap.enemies" :key="i"><span :class="enemy.kind === 'boss' ? 'boss' : enemy.kind === 'ex' ? 'purple' : 'red'">{{ enemy.kind === 'boss' ? 'Boss' : enemy.kind === 'ex' ? 'EX' : t('敌人') }} {{ i + 1 }}</span><span>{{ format(enemy.position) }}</span></div>
          <small>{{ t('敌人坐标以小地图左上角为原点') }}</small>
        </div>
        <section class="annotations-section">
          <div class="section-heading"><b>{{ t('已标注设施') }}</b><span>{{ t('底图坐标') }}</span></div>
          <p v-if="!annotations" class="map-note">{{ t('标注信息不可用，等待任务更新。') }}</p>
          <template v-else>
            <p class="map-note">{{ t('地图标注不代表机关已触发或电梯当前可用。') }}</p>
            <p v-if="!annotations.points.length" class="map-note">{{ t('本章暂无机关或电梯点标注。') }}</p>
            <div v-else class="annotation-list">
              <div v-for="point in annotations.points" :key="point.id" class="annotation-row">
                <span><b><CampaignMarker :kind="point.category === 'ground_mechanism' ? 'mechanism' : 'elevator'" :state="mechanismStates[point.id] ?? 'unknown'" :size="18" /> {{ point.category === 'ground_mechanism' ? t('地面机关') : t('电梯') }}</b><span>{{ point.label || point.id }}</span></span>
                <code>{{ format(point.position) }}<small v-if="point.category === 'ground_mechanism'">{{ t(mechanismStates[point.id] === 'on' ? '已开启' : mechanismStates[point.id] === 'off' ? '未开启' : '未检测到状态') }}</small></code>
              </div>
            </div>
            <div v-if="annotations.transfers.length" class="transfer-list">
              <div v-for="transfer in annotations.transfers" :key="transfer.id" class="transfer-row">
                <span>{{ t('电梯连接') }} · {{ transfer.id }} · {{ transfer.directed ? t('单向') : t('双向') }}</span>
                <code>{{ format(transfer.entry) }} {{ transfer.directed ? '→' : '↔' }} {{ format(transfer.exit) }}</code>
              </div>
            </div>
            <p v-else-if="elevators.length" class="map-note">{{ t('尚未标注电梯连接，不能据此判断传送路线。') }}</p>
          </template>
        </section>
      </section>
    </div>
    <p v-if="error" class="connection-error" role="status">{{ error }}</p>
  </article>
</template>

<style scoped>
.boss { color:var(--yellow); }
.campaign-monitor { flex:none; overflow:hidden; margin-bottom:18px; }
.campaign-head { display:flex; align-items:center; gap:14px; padding:18px 22px; border-bottom:1px solid var(--border); }
h3 { margin:0 0 5px; font-size:16px; }
.sub,.section-heading span,.map-note,dt,small { color:var(--text-2); font-size:12px; }
.sub b { color:var(--text); font-size:18px; margin-left:6px; }
.difficulty { padding:5px 9px; border-radius:6px; color:var(--accent); background:var(--accent-soft); font-size:12px; font-weight:600; }
.difficulty.hard { color:var(--red); background:var(--red-soft); }
.freshness { display:flex; flex-direction:column; gap:4px; margin-left:auto; text-align:right; font-size:12px; color:var(--text-2); }
.campaign-content { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(260px,1fr); }
.chapter-map-section,.observations { padding:18px; min-width:0; }
.chapter-map-section { border-right:1px solid var(--border); }
.section-heading { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:12px; font-size:13px; }
.minimap { max-height:240px; }
.marker-legend > span { display:inline-flex; align-items:center; gap:6px; }
.map-legend { border:1px solid var(--border); border-radius:8px; padding:10px 14px; margin-top:12px; display:flex; flex-direction:column; gap:9px; }
.map-legend .map-note { margin:0; }
.legend { display:flex; gap:6px 14px; flex-wrap:wrap; font-size:12px; align-items:center; }
.cyan { color:var(--accent); }.red { color:var(--red); }.purple { color:var(--violet); }.blocked { color:#ff5b5b; }.gold { color:#e0a800; }
.map-note { line-height:1.65; margin:10px 0 0; }
.target-details { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; border:1px solid var(--border); border-radius:8px; padding:10px 14px; margin-top:12px; }
.target-row { display:flex; flex-direction:column; gap:4px; font-size:12px; overflow-wrap:anywhere; }
.target-row b { font-size:12px; color:var(--text-2); font-weight:500; }
.target-row small,.annotation-row code small { color:var(--text-2); line-height:1.5; }
.annotation-row code small { display:block; text-align:right; font-family:var(--font-sans,sans-serif); }
.squad-positions { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin:14px 0 0; }
.squad-positions > div { display:flex; flex-direction:column; gap:4px; margin:0; }
dl > div,.enemy-list > div { display:flex; justify-content:space-between; gap:12px; margin:9px 0; }
dd { margin:0; font:12px var(--font-mono,monospace); }
.enemy-list { max-height:170px; overflow:auto; border-top:1px solid var(--border); margin-top:14px; padding-top:5px; font-size:12px; }
.mechanism { color:var(--green); }.elevator { color:var(--yellow); }
.annotations-section { border-top:1px solid var(--border); margin-top:18px; padding-top:16px; }
.annotations-section .section-heading { margin-bottom:6px; }
.annotation-list,.transfer-list { max-height:200px; overflow:auto; margin-top:10px; font-size:12px; }
.annotation-row { display:flex; justify-content:space-between; gap:12px; padding:7px 0; }
.annotation-row > span { display:flex; flex-direction:column; gap:3px; min-width:0; overflow-wrap:anywhere; }
.annotation-row b { font-weight:500; }.annotation-row code { flex:none; align-self:center; }
.transfer-list { border-top:1px solid var(--border); }.transfer-row { display:flex; flex-direction:column; gap:4px; padding:7px 0; overflow-wrap:anywhere; }
.annotation-row code,.transfer-row code { font:12px var(--font-mono,monospace); color:var(--text-2); }
.campaign-empty { min-height:250px; display:grid; place-items:center; padding:24px; color:var(--text-2); text-align:center; font-size:13px; }
.campaign-empty.compact { min-height:150px; }.connection-error { padding:0 18px 14px; color:var(--red); font-size:12px; }
@media(max-width:900px) { .campaign-content { grid-template-columns:1fr; }.chapter-map-section { border-right:0; border-bottom:1px solid var(--border); }.campaign-head { flex-wrap:wrap; padding:16px; }.section-heading { flex-wrap:wrap; } }
</style>
