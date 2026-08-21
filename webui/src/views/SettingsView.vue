<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { t } from '../i18n'
import { useSystemStore } from '../stores/system'
import { useUpdateStore } from '../stores/update'

const { systemStatus, updateInfo } = storeToRefs(useSystemStore())
const update = useUpdateStore()
const { isDesktopShell, desktopUpdate, desktopChecking, desktopApplying, updateChecking, updating, restarting } = storeToRefs(update)
const { checkDesktopUpdate, applyDesktopUpdate, checkUpdate, runUpdate, forceRestart } = update
</script>

<template>
  <section class="view">
    <article class="card task-hero">
      <div class="task-icon">🖥️</div>
      <div style="flex:1"><h2>{{ t('启动器更新') }}</h2><div class="sub">{{ t('更新nkas程序（exe）本身') }}<span v-if="!isDesktopShell" class="desktop-only-hint"> · {{ t('此功能仅在1.x版本中可用') }}</span></div>
        <div class="sub">{{ t('当前版本') }} <code class="ver-pill">{{ desktopUpdate?.currentVersion || '—' }}</code><span v-if="desktopUpdate?.updateAvailable" class="update-hint"> · {{ t('有新版本可用') }}</span><span v-else-if="desktopUpdate?.checked && !desktopUpdate?.error" class="sub"> · {{ t('已是最新') }}</span><span v-if="desktopUpdate?.error" class="update-hint"> · {{ desktopUpdate.error }}</span></div>
      </div>
      <button v-if="desktopUpdate?.updateAvailable" class="btn success" :disabled="!isDesktopShell || desktopApplying || desktopUpdate?.applying" @click="applyDesktopUpdate"><span v-if="desktopApplying || desktopUpdate?.applying" class="btn-spin"></span>{{ desktopApplying || desktopUpdate?.applying ? t('更新中…') : t('立即更新') }}</button>
      <button v-else class="btn primary" :disabled="!isDesktopShell || desktopChecking || desktopApplying || desktopUpdate?.checking || desktopUpdate?.applying" @click="checkDesktopUpdate"><span v-if="desktopChecking || desktopApplying || desktopUpdate?.checking || desktopUpdate?.applying" class="btn-spin"></span>{{ desktopApplying || desktopUpdate?.applying ? t('更新中…') : (desktopChecking || desktopUpdate?.checking ? t('检查中…') : t('检查更新')) }}</button>
    </article>
    <article class="card task-hero">
      <div class="task-icon">🚀</div>
      <div style="flex:1"><h2>{{ t('源码更新') }}</h2><div class="sub">{{ t('当前版本') }} <code class="ver-pill">{{ systemStatus.version }}</code><span v-if="Number(updateInfo.state) === 1" class="update-hint"> · {{ t('有新版本可用') }}</span><span v-else-if="Number(updateInfo.state) === 0" class="sub"> · {{ t('已是最新') }}</span><span v-else-if="updateInfo.state === 'failed'" class="update-error"> · {{ updateInfo.error ? t('检查更新失败') : t('更新失败') }}<span v-if="updateInfo.error">：{{ updateInfo.error }}</span></span></div></div>
      <button v-if="Number(updateInfo.state) === 1" class="btn success" :disabled="updating" @click="runUpdate"><span v-if="updating" class="btn-spin"></span>{{ updating ? t('更新中…') : t('立即更新') }}</button>
      <!-- "failed" with an error message means the *check* failed (e.g.
           network), so offer re-check instead of a full update+restart;
           an empty error means a real update run failed, offer retry. -->
      <button v-else-if="updateInfo.state === 'failed' && !updateInfo.error" class="btn danger" :disabled="updating" @click="runUpdate"><span v-if="updating" class="btn-spin"></span>{{ updating ? t('更新中…') : t('重试更新') }}</button>
      <button v-else class="btn primary" :disabled="updating || updateChecking || updateInfo.state === 'checking'" @click="checkUpdate">{{ updating ? t('更新中…') : (updateChecking || updateInfo.state === 'checking' ? t('检查中…') : t('检查更新')) }}</button>
      <button class="btn danger" :disabled="restarting" @click="forceRestart"><span v-if="restarting" class="btn-spin"></span>{{ restarting ? t('重启中…') : t('强制重启') }}</button>
    </article>
    <article class="card group-card">
      <div class="group-head"><h4>{{ t('更新记录') }}</h4></div>
      <div class="group-body history-body">
        <div v-for="commit in updateInfo.history || []" :key="commit[0]" class="history-row" :class="{ current: updateInfo.local && commit[0] === updateInfo.local[0] }"><span class="msg">{{ commit[3] }}</span><small><code>{{ commit[0] }}</code><span v-if="updateInfo.local && commit[0] === updateInfo.local[0]" class="current-pill">{{ t('当前版本') }}</span>{{ String(commit[2] || '').slice(0, 10) }}</small></div>
      </div>
    </article>
  </section>
</template>
