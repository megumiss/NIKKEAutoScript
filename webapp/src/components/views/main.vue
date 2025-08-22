<template>
  <iframe :src="webuiUrl"></iframe>
</template>

<script setup>
import {webuiUrl} from '@/config/config';</script>
<style lang="stylus" scoped>
*
  border-width 0
  width 100vw
  height 100vh
  overflow hidden

</style>