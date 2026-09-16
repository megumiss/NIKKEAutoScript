<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { t } from '../i18n'
import { useCookieSyncStore } from '../stores/cookieSync'

const store = useCookieSyncStore()
const { pending, visible, busy } = storeToRefs(store)
const { confirm, reject } = store
</script>

<template>
  <div v-if="visible && pending" class="modal-mask" role="presentation">
    <section class="modal-card cookie-sync-modal" role="dialog" aria-modal="true" aria-labelledby="cookie-sync-title">
      <h3 id="cookie-sync-title">{{ t('确认同步 Cookie') }}</h3>
      <p class="modal-text">{{ t('浏览器扩展请求将 Cookie 同步到 NKAS。确认后才会写入配置。') }}</p>
      <dl class="cookie-sync-details">
        <div><dt>{{ t('实例') }}</dt><dd>{{ pending.instance }}</dd></div>
        <div v-if="pending.uid"><dt>UID</dt><dd>{{ pending.uid }}</dd></div>
        <div v-if="pending.username"><dt>{{ t('账号') }}</dt><dd>{{ pending.username }}</dd></div>
      </dl>
      <div class="modal-actions">
        <button class="btn" :disabled="busy" @click="reject">{{ t('拒绝') }}</button>
        <button class="btn primary" :disabled="busy" @click="confirm">{{ busy ? t('处理中…') : t('确认同步') }}</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.cookie-sync-modal { max-width: 440px; }
.cookie-sync-details { display: grid; gap: 8px; margin: 16px 0 4px; }
.cookie-sync-details div { display: flex; justify-content: space-between; gap: 20px; padding: 8px 10px; border-radius: 8px; background: var(--card-2); }
.cookie-sync-details dt { color: var(--text-2); }
.cookie-sync-details dd { margin: 0; max-width: 65%; overflow-wrap: anywhere; text-align: right; }
</style>
