<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { t } from '../i18n'
import { useBlaLoginStore } from '../stores/blaLogin'

const blaLogin = useBlaLoginStore()
const { blaLoginOpen, blaLoginState, blaShotUrl } = storeToRefs(blaLogin)
const { blaStateText, closeBlaLogin, blaDragStart, blaDragMove, blaDragEnd } = blaLogin
</script>

<template>
  <div v-if="blaLoginOpen" class="modal-mask">
    <div class="modal-card">
      <h3>{{ t('登录获取 Cookie（BETA）') }}</h3>
      <p class="modal-text">{{ blaStateText() }}</p>
      <template v-if="blaLoginState === 'captcha'">
        <img :src="blaShotUrl" draggable="false" alt="captcha" style="display:block;margin:0 auto;max-width:100%;touch-action:none;user-select:none;-webkit-user-drag:none;cursor:grab" @pointerdown="blaDragStart" @pointermove="blaDragMove" @pointerup="blaDragEnd">
      </template>
      <div class="modal-actions">
        <button class="btn danger" @click="closeBlaLogin">{{ t('取消') }}</button>
      </div>
    </div>
  </div>
</template>
