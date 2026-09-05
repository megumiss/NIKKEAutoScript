<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSelect from './AppSelect.vue'
import { api } from '../api/client'
import { t } from '../i18n'
import { useModalStore } from '../stores/modal'

const modalStore = useModalStore()
const { modal, originOptions, deployTemplateOptions, modalConfirmMessage, modalAlertTitle, modalAlertMessage } = storeToRefs(modalStore)
const { confirmModal } = modalStore
// 路由切换时关闭弹窗，避免弹窗残留遮挡新页面（移动端尤其明显）
const route = useRoute()
watch(() => route.fullPath, () => { modal.value.type = '' })
// 头像由后端托管（/avatars/），列表从 API 获取。
const avatarFiles = ref<string[]>([])
onMounted(async () => { try { avatarFiles.value = await api.get('/api/avatars') } catch { avatarFiles.value = [] } })
</script>

<template>
  <div v-if="modal.type" class="modal-mask" @click.self="modal.type = ''">
    <div class="modal-card">
      <h3>{{ modal.type === 'create' ? t('新建实例') : modal.type === 'avatar' ? t('更改头像') : modal.type === 'rename' ? t('重命名') : modal.type === 'resetDeploy' ? t('还原默认') : modal.type === 'confirm' ? t('确认') : modal.type === 'alert' ? modalAlertTitle : t('删除') }}</h3>
      <template v-if="modal.type === 'create'">
        <label class="modal-field">{{ t('名称') }}<input v-model="modal.name" @keyup.enter="confirmModal"></label>
        <label class="modal-field">{{ t('复制来源实例') }}<AppSelect v-model="modal.origin" :options="originOptions"/></label>
        <div class="modal-field">
          <span class="avatar-picker-label">{{ t('头像') }}</span>
          <div class="avatar-picker">
            <button type="button" class="avatar-opt text" :class="{ active: !modal.avatar }" @click="modal.avatar = ''">{{ t('无') }}</button>
            <button v-for="file in avatarFiles" :key="file" type="button" class="avatar-opt" :class="{ active: modal.avatar === file }" :title="file" @click="modal.avatar = file"><img :src="`/avatars/${file}`" alt=""></button>
          </div>
        </div>
      </template>
      <template v-else-if="modal.type === 'avatar'">
        <p class="modal-text">{{ t('为') }} {{ modal.name }} {{ t('选择头像') }}</p>
        <div class="modal-field">
          <div class="avatar-picker">
            <button type="button" class="avatar-opt text" :class="{ active: !modal.avatar }" @click="modal.avatar = ''">{{ t('无') }}</button>
            <button v-for="file in avatarFiles" :key="file" type="button" class="avatar-opt" :class="{ active: modal.avatar === file }" :title="file" @click="modal.avatar = file"><img :src="`/avatars/${file}`" alt=""></button>
          </div>
        </div>
      </template>
      <template v-else-if="modal.type === 'rename'">
        <label class="modal-field">{{ t('名称') }}<input v-model="modal.name" @keyup.enter="confirmModal"></label>
      </template>
      <template v-else-if="modal.type === 'resetDeploy'">
        <p class="modal-text">{{ t('将全部部署配置还原为默认值？') }}{{ t('此操作不可恢复。') }}</p>
        <label class="modal-field">{{ t('模板') }}<AppSelect v-model="modal.template" :options="deployTemplateOptions"/></label>
      </template>
      <p v-else-if="modal.type === 'confirm'" class="modal-text">{{ modalConfirmMessage }}</p>
      <p v-else-if="modal.type === 'alert'" class="modal-text">{{ modalAlertMessage }}</p>
      <p v-else class="modal-text">{{ t('删除') }} {{ modal.name }}？{{ t('此操作不可恢复。') }}</p>
      <div class="modal-actions">
        <button v-if="modal.type !== 'alert'" class="btn" @click="modal.type = ''">{{ t('取消') }}</button>
        <button class="btn" :class="modal.type === 'delete' || modal.type === 'resetDeploy' ? 'danger' : 'primary'" :disabled="modal.busy || ((modal.type === 'create' || modal.type === 'rename') && !modal.name.trim())" @click="confirmModal">{{ modal.type === 'alert' ? t('知道了') : t('确定') }}</button>
      </div>
    </div>
  </div>
</template>
