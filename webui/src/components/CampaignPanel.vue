<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api/client'
import { t, uiLanguage } from '../i18n'
import { useInstancesStore } from '../stores/instances'
import CampaignMap, { type Enemy, type Point } from './CampaignMap.vue'

const props = defineProps<{ name: string }>()
type Snapshot = {
  chapter: number; difficulty: 'normal' | 'hard'; captured_at: number
  map: { size: Point; image_sha256: string } | null
  minimap: { image: string; size: Point; squad: Point | null; enemies: Enemy[]; mode: string } | null
  localization: string; squad: Point | null; enemies: Enemy[]
}
const instances = useInstancesStore()
const snapshot = ref<Snapshot | null>(null)
const running = ref(false)
const error = ref('')
const mapError = ref(false)
let generation = 0
const mapUrl = computed(() => snapshot.value?.map ? `/api/${encodeURIComponent(props.name)}/campaign/map/${snapshot.value.chapter}?v=${snapshot.value.map.image_sha256}` : '')
const updatedAt = computed(() => snapshot.value ? new Date(snapshot.value.captured_at * 1000).toLocaleTimeString(uiLanguage.value) : '')
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
        <div class="section-heading"><b>{{ t('章节地图') }}</b><span>{{ t('普通 / 困难共用底图') }}</span></div>
        <CampaignMap v-if="snapshot.map && !mapError" :image="mapUrl" :size="snapshot.map.size" :squad="snapshot.squad" :enemies="snapshot.enemies" :label="t('章节地图')" @error="mapError = true" />
        <div v-else class="campaign-empty">{{ t('章节底图不可用') }}</div>
        <div class="legend"><span class="cyan">○ {{ t('小队') }}</span><span class="red">◆ {{ t('敌人') }}</span><span class="purple">◆ EX</span></div>
        <p class="map-note">{{ snapshot.localization === 'accepted' ? t('位置已匹配到章节底图') : t('底图位置未确认，请查看小地图。') }}</p>
      </section>
      <section class="observations">
        <div class="section-heading"><b>{{ t('小地图') }}</b><span>{{ snapshot.minimap?.enemies.length ?? '—' }} {{ t('个可见敌人') }}</span></div>
        <CampaignMap v-if="snapshot.minimap" class="minimap" :image="snapshot.minimap.image" :size="snapshot.minimap.size" :squad="snapshot.minimap.squad" :enemies="snapshot.minimap.enemies" :label="t('小地图')" />
        <div v-else class="campaign-empty compact">{{ t('未检测到小地图') }}</div>
        <dl>
          <div><dt>{{ t('小队位置') }} · {{ t('底图') }}</dt><dd>{{ format(snapshot.squad) }}</dd></div>
          <div><dt>{{ t('小队位置') }} · {{ t('小地图') }}</dt><dd>{{ format(snapshot.minimap?.squad) }}</dd></div>
        </dl>
        <div v-if="snapshot.minimap?.enemies.length" class="enemy-list">
          <div v-for="(enemy, i) in snapshot.minimap.enemies" :key="i"><span :class="enemy.kind === 'ex' ? 'purple' : 'red'">{{ enemy.kind === 'ex' ? 'EX' : t('敌人') }} {{ i + 1 }}</span><span>{{ format(enemy.position) }}</span></div>
          <small>{{ t('敌人坐标以小地图左上角为原点') }}</small>
        </div>
      </section>
    </div>
    <p v-if="error" class="connection-error" role="status">{{ error }}</p>
  </article>
</template>

<style scoped>
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
.legend { display:flex; gap:16px; flex-wrap:wrap; padding-top:12px; font-size:12px; }
.cyan { color:var(--accent); }.red { color:var(--red); }.purple { color:var(--violet); }
.map-note { line-height:1.65; margin:10px 0 0; }
dl { margin:16px 0; }dl > div,.enemy-list > div { display:flex; justify-content:space-between; gap:12px; margin:9px 0; }
dd { margin:0; font:12px var(--font-mono,monospace); }
.enemy-list { max-height:170px; overflow:auto; border-top:1px solid var(--border); margin-top:14px; padding-top:5px; font-size:12px; }
.campaign-empty { min-height:250px; display:grid; place-items:center; padding:24px; color:var(--text-2); text-align:center; font-size:13px; }
.campaign-empty.compact { min-height:150px; }.connection-error { padding:0 18px 14px; color:var(--red); font-size:12px; }
@media(max-width:900px) { .campaign-content { grid-template-columns:1fr; }.chapter-map-section { border-right:0; border-bottom:1px solid var(--border); }.campaign-head { flex-wrap:wrap; padding:16px; }.section-heading { flex-wrap:wrap; } }
</style>
