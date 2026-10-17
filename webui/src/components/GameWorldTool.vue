<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { t } from '../i18n'
import { useToastStore } from '../stores/toast'
import AppIcon from './AppIcon.vue'
import AppSelect from './AppSelect.vue'

type GameWorldState = {
  supported: boolean
  readable: boolean
  world: string
  world_name: string
  message?: string
}

const toast = useToastStore()
const supported = ref<boolean | null>(null)
const readable = ref(false)
const action = ref<'' | 'refresh' | 'write'>('')
const busy = computed(() => action.value !== '')
const currentWorld = ref('')
const currentName = ref('')
const selectedWorld = ref('')
const message = ref('')
const worldOptions = computed(() => [
  { value: 'global', label: t('全球（GLOBAL）') },
  { value: 'jp', label: t('日服（JAPAN）') },
  { value: 'na', label: t('美服（NA）') },
  { value: 'kr', label: t('韩服（KOREA）') },
  { value: 'sea', label: t('东南亚（SEA）') },
])
const currentLabel = computed(() => worldOptions.value.find(item => item.value === currentWorld.value)?.label
  || currentName.value || (readable.value ? t('尚未选择区服') : '—'))
const canWrite = computed(() => supported.value === true && readable.value
  && worldOptions.value.some(item => item.value === selectedWorld.value))

function applyState(data: GameWorldState) {
  supported.value = data.supported
  readable.value = data.readable
  currentWorld.value = data.world
  currentName.value = data.world_name
  message.value = data.message || ''
  selectedWorld.value = currentWorld.value
}
async function refresh() {
  if (busy.value) return
  action.value = 'refresh'
  try { applyState(await api.get('/api/tools/game_world')) } catch (exception: any) {
    readable.value = false
    currentWorld.value = ''
    currentName.value = ''
    message.value = exception.message
  } finally { action.value = '' }
}
async function writeWorld() {
  if (busy.value || !canWrite.value) return
  action.value = 'write'
  try {
    applyState(await api.post('/api/tools/game_world', { world: selectedWorld.value }))
    toast.notify(t('区服已写入并通过回读校验'))
  } catch (exception: any) {
    readable.value = false
    currentWorld.value = ''
    currentName.value = ''
    message.value = exception.message
    toast.error = t(exception.message)
  } finally { action.value = '' }
}
onMounted(refresh)
</script>

<template>
  <article class="card group-card" :aria-busy="busy">
    <div class="group-head"><h4>{{ t('游戏区服') }}</h4></div>
    <div class="group-body hosts-body">
      <p class="fhelp">{{ t('建议优先使用实例中的「PC 客户端 → 游戏区服」设置，本工具用于临时手动切换。') }}</p>
      <p class="fhelp">{{ t('仅修改 NKAS 后端所在电脑、运行用户的国际服 PC 客户端配置，港澳台客户端不适用。游戏运行时可能覆盖设置。') }}</p>
      <div v-if="supported === false" class="hosts-unsupported"><AppIcon name="alert-triangle" :size="14" /> {{ t('仅支持 Windows 国际服 PC 客户端。') }}</div>
      <p v-if="message" class="hosts-unsupported" role="status">{{ t(message) }}</p>
      <div class="world-controls">
        <div class="hosts-region"><span class="hosts-region-label">{{ t('当前区服') }}</span><strong role="status">{{ currentLabel }}</strong></div>
        <div class="hosts-region">
          <span class="hosts-region-label" id="game-world-label">{{ t('目标区服') }}</span>
          <AppSelect v-model="selectedWorld" aria-labelledby="game-world-label" :options="worldOptions" :placeholder="t('请选择区服')" :disabled="busy || supported !== true || !readable" />
        </div>
      </div>
      <div class="hosts-actions">
        <button class="btn" :disabled="busy" @click="refresh"><AppIcon name="refresh" :size="14" /> {{ action === 'refresh' ? t('读取中…') : t('刷新状态') }}</button>
        <button class="btn primary" :disabled="busy || !canWrite" @click="writeWorld"><AppIcon name="check" :size="14" /> {{ action === 'write' ? t('写入中…') : t('写入区服') }}</button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.world-controls { display: grid; gap: 12px; max-width: 480px; }
.world-controls .hosts-region { margin-bottom: 0; gap: 12px; }
.world-controls .hosts-region-label { flex: none; width: 80px; }
.world-controls .hosts-region > :last-child { flex: 1; min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 560px) {
  .world-controls .hosts-region { flex-wrap: wrap; }
  .world-controls .hosts-region-label { width: 100%; }
}
</style>
