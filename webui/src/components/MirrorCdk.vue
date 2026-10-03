<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { t } from '../i18n'
import { useToastStore } from '../stores/toast'

const toast = useToastStore()
const configured = ref(false)
const cdk = ref('')
const busy = ref(false)
onMounted(async () => {
  try { configured.value = (await api.get('/api/system/mirror-cdk')).configured }
  catch (error: any) { toast.error = error.message }
})
async function save(clear = false) {
  if (busy.value) return
  busy.value = true
  try {
    const result = await api.post('/api/system/mirror-cdk', clear ? { clear: true } : { cdk: cdk.value })
    configured.value = result.configured
    cdk.value = ''
    toast.notify(t('已保存'))
  } catch (error: any) { toast.error = error.message }
  finally { busy.value = false }
}
</script>

<template>
  <div class="field field-wide">
    <div class="field-label">
      <label class="fname" for="mirror-cdk">{{ t('Mirror 酱 CDK（可选）') }}</label>
      <div class="fhelp">{{ t('留空仍可检查更新。有新版本却没有下载链接时直接报错。保存后下次检查生效。') }}</div>
      <a href="https://mirrorchyan.com/?source=NKAS" target="_blank" rel="noopener noreferrer">Mirror 酱</a>
    </div>
    <form class="cdk-controls" @submit.prevent="save()">
      <input id="mirror-cdk" v-model="cdk" type="password" autocomplete="new-password" maxlength="512" :disabled="busy" :placeholder="configured ? '••••••••' : t('未配置')">
      <button class="btn" :disabled="busy || !cdk.trim()" type="submit">{{ t('保存') }}</button>
      <button class="btn danger" :disabled="busy || !configured" type="button" @click="save(true)">{{ t('清除') }}</button>
    </form>
  </div>
</template>

<style scoped>
.cdk-controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.cdk-controls input { flex: 1; min-width: 160px; }
</style>
