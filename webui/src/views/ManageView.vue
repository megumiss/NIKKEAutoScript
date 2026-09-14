<script setup lang="ts">
import { storeToRefs } from 'pinia'
import AppIcon from '../components/AppIcon.vue'
import { exportTextFile } from '../composables/useFileExport'
import { t } from '../i18n'
import { useInstancesStore } from '../stores/instances'
import { useModalStore } from '../stores/modal'
import { useToastStore } from '../stores/toast'

const instancesStore = useInstancesStore()
const toast = useToastStore()
const { instances, dragIndex, dragOverIndex } = storeToRefs(instancesStore)
const { displayStatus, displayStatusClass, initials, avatarUrl, onDragStart, onDragOver, onDragEnd, onDrop, saveRemark, importInstance } = instancesStore
const { openCreateModal, openRenameModal, openDeleteModal, openAvatarModal } = useModalStore()

async function exportInstance(name: string, mod: string) {
  const filename = mod === 'nkas' ? `${name}.json` : `${name}.${mod}.json`
  try {
    const path = await exportTextFile(`/api/${encodeURIComponent(name)}/export`, filename)
    toast.notify(path ? `${t('导出成功')}: ${path}` : t('导出成功'))
  } catch (exception: any) {
    toast.error = exception?.message || t('导出失败')
  }
}
</script>

<template>
  <section class="view">
    <article class="card task-hero">
      <div class="task-icon"><AppIcon name="layers" :size="22" /></div>
      <div style="flex:1"><h2>{{ t('多开') }}</h2><div class="sub">{{ t('实例总数') }}: {{ instances.length }}</div></div>
      <button class="btn primary" @click="openCreateModal"><AppIcon name="plus" :size="15" color="currentColor" /> {{ t('新建实例') }}</button>
      <label class="btn"><AppIcon name="import" :size="14" color="currentColor" /> {{ t('导入配置') }}<input type="file" accept=".json" hidden @change="importInstance"></label>
    </article>
    <article class="card manage-table" style="overflow:hidden">
      <table>
        <colgroup><col style="width:22%"><col style="width:10%"><col style="width:15%"><col><col style="width:260px"></colgroup>
        <thead><tr><th>{{ t('名称') }}</th><th>Mod</th><th>{{ t('状态') }}</th><th>{{ t('备注') }}</th><th>{{ t('操作') }}</th></tr></thead>
        <tbody>
          <tr v-for="(instance, index) in instances" :key="instance.name"
              :class="{ dragging: dragIndex === index, 'drag-over': dragIndex >= 0 && dragOverIndex === index && dragOverIndex !== dragIndex }"
              @dragstart="onDragStart(index, $event)" @dragover="onDragOver(index, $event)" @drop="onDrop" @dragend="onDragEnd">
            <td :data-label="t('名称')"><span class="cell-inst"><span class="drag-handle" draggable="true" :title="t('拖动排序')"><AppIcon name="sort-v" :size="14" /></span><span class="inst-avatar avatar-clickable" :title="t('点击更改头像')" :class="{ idle: displayStatusClass(instance.name, instance.state, instance.current_task) === 'idle' }" @click="openAvatarModal(instance.name)"><img v-if="avatarUrl(instance.avatar)" class="inst-avatar-img" :src="avatarUrl(instance.avatar)" :alt="instance.name"><template v-else>{{ initials(instance.name) }}</template><span class="ring" :class="displayStatusClass(instance.name, instance.state, instance.current_task)"></span></span>{{ instance.name }}</span></td>
            <td :data-label="t('Mod')">{{ instance.mod }}</td>
            <td :data-label="t('状态')"><span class="status-pill" :class="displayStatusClass(instance.name, instance.state, instance.current_task)">{{ displayStatus(instance.name, instance.state, instance.current_task) }}</span></td>
            <td :data-label="t('备注')"><input class="remark-input" :value="instance.remark" placeholder="—" @change="saveRemark(instance, $event)"></td>
            <td :data-label="t('操作')"><span class="row-actions"><button class="btn sm" @click="exportInstance(instance.name, instance.mod)"><AppIcon name="download" :size="13" color="currentColor" /> {{ t('导出') }}</button> <button class="btn sm" :disabled="instance.state === 1" @click="openRenameModal(instance.name)"><AppIcon name="edit" :size="13" /> {{ t('重命名') }}</button> <button class="btn danger sm" :disabled="instance.state === 1" @click="openDeleteModal(instance.name)"><AppIcon name="trash" :size="13" /> {{ t('删除') }}</button></span></td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>
