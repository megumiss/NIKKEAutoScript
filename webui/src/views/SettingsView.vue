<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import AppIcon from '../components/AppIcon.vue'
import { t } from '../i18n'
import { useSystemStore } from '../stores/system'
import { useUpdateStore } from '../stores/update'

const { systemStatus, updateInfo } = storeToRefs(useSystemStore())
const update = useUpdateStore()
const { isDesktopShell, desktopUpdate, desktopChecking, desktopApplying, updateChecking, updating, restarting } = storeToRefs(update)
const { checkDesktopUpdate, applyDesktopUpdate, checkUpdate, runUpdate, forceRestart } = update
const historyRecords = computed(() => {
  const history = updateInfo.value.history
  const commits = Array.isArray(history)
    ? history.filter(commit => Array.isArray(commit) && commit[0]) : []
  const localSha = updateInfo.value.local?.[0]
  const records = commits.map(commit => ({
    key: `git-${commit[0]}`, message: String(commit[3] || ''), revision: String(commit[0]),
    date: String(commit[2] || '').slice(0, 10), current: commit[0] === localSha, label: '',
  }))

  function releaseRecords(note: unknown, version: string, launcher = false) {
    if (typeof note !== 'string') return []
    return note.split(/\r?\n/).map(line => line.trim().replace(/^[-*]\s+/, '')).filter(Boolean).flatMap((message, index) => {
      // Pack notes may include a commit SHA; avoid duplicating an installed commit.
      const match = launcher ? null : message.match(/\s+\(([0-9a-f]{7,64})\)$/i)
      const sha = match?.[1]
      if (sha && commits.some(commit => String(commit[0]).startsWith(sha) || sha.startsWith(String(commit[0])))) return []
      const revision = sha || version
      return [{
        key: `${launcher ? 'launcher' : 'source'}-${version}-${index}`,
        message: match ? message.slice(0, match.index) : message, revision, date: '',
        current: !launcher && (sha ? Boolean(localSha && (sha.startsWith(localSha) || localSha.startsWith(sha)))
          : Boolean(version && version === updateInfo.value.local_version)),
        label: launcher ? t('启动器更新') : '',
      }]
    })
  }

  const sourceNotes = updateInfo.value.source === 'mirrorchyan'
    ? releaseRecords(updateInfo.value.release_note, updateInfo.value.remote_version || '') : []
  const launcherNotes = desktopUpdate.value?.source === 'mirrorchyan'
    ? releaseRecords(desktopUpdate.value.releaseNote, desktopUpdate.value.remoteVersion || '', true) : []
  return [...sourceNotes, ...launcherNotes, ...records]
})
</script>

<template>
  <section class="view updates-view">
    <article class="card update-card">
      <div class="task-hero">
        <div class="task-icon"><AppIcon name="monitor" :size="22" /></div>
        <div class="update-summary"><h2>{{ t('启动器更新') }}</h2><div class="sub">{{ t('更新nkas程序（exe）本身') }} · {{ desktopUpdate?.source === 'mirrorchyan' ? t('Mirror酱') : t('Git源') }}<span v-if="!isDesktopShell" class="desktop-only-hint"> · {{ t('此功能仅在1.x版本中可用') }}</span></div>
          <div class="sub">{{ t('当前版本') }} <code class="ver-pill">{{ desktopUpdate?.currentVersion || '—' }}</code><span v-if="desktopUpdate?.updateAvailable" class="update-hint"> · {{ t('有新版本可用') }}</span><span v-else-if="desktopUpdate?.checked && !desktopUpdate?.error" class="sub"> · {{ t('已是最新') }}</span><span v-if="desktopUpdate?.error" class="update-hint"> · {{ desktopUpdate.error }}</span></div>
          <p v-if="desktopUpdate?.needsCdk && desktopUpdate?.updateAvailable" class="update-cdk-hint">{{ t('下载需要有效 CDK，请前往部署页填写，或切换原更新源。') }}</p>
        </div>
        <button v-if="desktopUpdate?.updateAvailable && !desktopUpdate?.needsCdk" class="btn success" :disabled="updating || !isDesktopShell || desktopApplying || desktopUpdate?.applying" @click="applyDesktopUpdate"><span v-if="desktopApplying || desktopUpdate?.applying" class="btn-spin"></span><AppIcon name="download" :size="15" color="currentColor" /> {{ desktopApplying || desktopUpdate?.applying ? t('更新中…') : t('立即更新') }}</button>
        <button v-else class="btn primary" :disabled="updating || !isDesktopShell || desktopChecking || desktopApplying || desktopUpdate?.checking || desktopUpdate?.applying" @click="checkDesktopUpdate"><span v-if="desktopChecking || desktopApplying || desktopUpdate?.checking || desktopUpdate?.applying" class="btn-spin"></span><AppIcon name="refresh" :size="15" color="currentColor" /> {{ desktopApplying || desktopUpdate?.applying ? t('更新中…') : (desktopChecking || desktopUpdate?.checking ? t('检查中…') : t('检查更新')) }}</button>
      </div>
    </article>
    <article class="card update-card">
      <div class="task-hero">
        <div class="task-icon"><AppIcon name="rocket" :size="22" /></div>
        <div class="update-summary"><h2>{{ t('源码更新') }}</h2><div class="sub">{{ t('当前版本') }} <code class="ver-pill">{{ updateInfo.local_version || systemStatus.version }}</code> · {{ updateInfo.source === 'mirrorchyan' ? t('Mirror酱') : t('Git源') }}<span v-if="Number(updateInfo.state) === 1" class="update-hint"> · {{ t('有新版本可用') }}</span><span v-else-if="Number(updateInfo.state) === 0" class="sub"> · {{ t('已是最新') }}</span><span v-else-if="updateInfo.state === 'failed'" class="update-error"> · {{ updateInfo.error ? t('检查更新失败') : t('更新失败') }}<span v-if="updateInfo.error || updateInfo.update_error">：{{ updateInfo.error || updateInfo.update_error }}</span></span></div>
          <p v-if="updateInfo.needs_cdk && Number(updateInfo.state) === 1" class="update-cdk-hint">{{ t('下载需要有效 CDK，请前往部署页填写，或切换原更新源。') }}</p>
        </div>
        <div class="update-actions">
          <button v-if="Number(updateInfo.state) === 1 && !updateInfo.needs_cdk" class="btn success" :disabled="updating || desktopApplying || desktopUpdate?.applying" @click="runUpdate"><span v-if="updating" class="btn-spin"></span><AppIcon name="download" :size="15" color="currentColor" /> {{ updating ? t('更新中…') : t('立即更新') }}</button>
          <!-- "failed" with an error message means the *check* failed (e.g.
               network), so offer re-check instead of a full update+restart;
               an empty error means a real update run failed, offer retry. -->
          <button v-else-if="updateInfo.state === 'failed' && !updateInfo.error" class="btn danger" :disabled="updating || desktopApplying || desktopUpdate?.applying" @click="runUpdate"><span v-if="updating" class="btn-spin"></span><AppIcon name="refresh" :size="15" color="currentColor" /> {{ updating ? t('更新中…') : t('重试更新') }}</button>
          <button v-else class="btn primary" :disabled="updating || desktopApplying || desktopUpdate?.applying || updateChecking || updateInfo.state === 'checking'" @click="checkUpdate"><AppIcon name="refresh" :size="15" color="currentColor" /> {{ updating ? t('更新中…') : (updateChecking || updateInfo.state === 'checking' ? t('检查中…') : t('检查更新')) }}</button>
          <button class="btn danger" :disabled="restarting" @click="forceRestart"><span v-if="restarting" class="btn-spin"></span><AppIcon name="power" :size="15" /> {{ restarting ? t('重启中…') : t('强制重启') }}</button>
        </div>
      </div>
    </article>
    <article v-if="historyRecords.length" class="card group-card">
      <div class="group-head"><h4>{{ t('更新记录') }}</h4></div>
      <div class="group-body history-body">
        <div v-for="record in historyRecords" :key="record.key" class="history-row" :class="{ current: record.current }">
          <span class="msg">{{ record.message }}</span>
          <small><code v-if="record.revision">{{ record.revision }}</code><span v-if="record.current" class="current-pill">{{ t('当前版本') }}</span><span v-if="record.label" class="history-label">{{ record.label }}</span>{{ record.date }}</small>
        </div>
      </div>
    </article>
    <p v-else-if="Array.isArray(updateInfo.history)" class="update-history-empty" role="status">
      <AppIcon name="file-text" :size="18" color="currentColor" /> {{ t('暂无可显示的更新记录') }}
    </p>
  </section>
</template>

<style scoped>
.updates-view { display: flex; flex-direction: column; gap: 16px; }
.updates-view > * { flex-shrink: 0; }
.update-card { overflow: hidden; }
.update-card .task-hero { margin-bottom: 0; }
.update-summary { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.update-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.update-cdk-hint { margin-top: 8px; color: var(--text-2); font-size: 12.5px; line-height: 1.6; }
.history-label { margin-right: 10px; }
.update-history-empty { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 20px 16px; color: var(--text-2); font-size: 13px; }
@media (max-width: 700px) {
  .update-actions { width: 100%; justify-content: flex-end; }
}
</style>
