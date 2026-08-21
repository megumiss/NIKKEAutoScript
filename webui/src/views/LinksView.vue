<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { t } from '../i18n'
import { useLinksStore } from '../stores/links'

const links = useLinksStore()
const { webLinks, webUrl, webLoaded, webBusy, webFrameKey } = storeToRefs(links)
const { webFrameSrc, webLink, webLinkName, openWeb, refreshWeb } = links
</script>

<template>
  <section class="view web-view">
    <div v-if="webLoaded && !webLinks.length" class="card web-empty">{{ t('暂无可用链接') }}</div>
    <template v-else>
      <div class="web-tabs">
        <button v-for="link in webLinks" :key="link.url" class="web-tab" :class="{ active: webUrl === link.url }" @click="openWeb(link.url)">{{ webLinkName(link) }}</button>
        <span class="web-login-hint">⚠ {{ t('此页面无法进行登录操作') }}</span>
        <button v-if="webUrl" class="web-tab web-refresh" type="button" :title="t('刷新')" @click="refreshWeb">⟳</button>
        <a v-if="webUrl" class="web-tab web-open" :href="webUrl" target="_blank" rel="noopener">{{ t('外部打开') }}</a>
      </div>
      <div class="web-frame-wrap">
        <iframe v-if="webUrl" :key="webFrameKey" class="web-frame" :src="webFrameSrc(webLink(webUrl))" @load="webBusy = false" @error="webBusy = false"></iframe>
        <div v-if="webBusy" class="web-loading">{{ t('加载中…') }}</div>
        <div v-if="!webUrl" class="web-empty">{{ t('请选择链接') }}</div>
      </div>
    </template>
  </section>
</template>
