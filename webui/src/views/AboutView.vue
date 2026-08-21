<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { brandIcon } from '../utils'
import { useSystemStore } from '../stores/system'

const { systemStatus } = storeToRefs(useSystemStore())
</script>

<template>
  <section class="view">
    <article class="card about-panel">
      <div class="about-hero">
        <img class="brand-logo brand-logo-img" :src="brandIcon" alt="NKAS">
        <div><h2>NKAS · NIKKEAutoScript</h2><div class="sub">NIKKEAutoScript · {{ systemStatus.version }}</div></div>
      </div>
      <p>NKAS is a free open source software, if you paid for NKAS from any channel, please refund.<br>NKAS 是一款免费开源软件，如果你在任何渠道付费购买了NKAS，请退款。</p>
      <h3>Project / 项目</h3>
      <p>
        Project repository / 项目地址：<a href="https://github.com/megumiss/NIKKEAutoScript" target="_blank">https://github.com/megumiss/NIKKEAutoScript</a><br>
        Detailed guide / 详细指南：<a href="https://github.com/megumiss/NIKKEAutoScript/wiki" target="_blank">https://github.com/megumiss/NIKKEAutoScript/wiki</a>
      </p>
      <h3>💡 Need help / 寻求帮助</h3>
      <p>
        If you encounter any issues while using, you can get help through the following ways:<br>
        如果在使用过程中遇到问题，您可以通过以下方式获取帮助：<br>
        Submit an issue / 提交问题：<a href="https://github.com/megumiss/NIKKEAutoScript/issues" target="_blank">GitHub Issues</a><br>
        QQ group / 划水 QQ 群：823265807
      </p>
      <h3>Support / 支持项目</h3>
      <p>
        If you like this project, you can buy the author a cup of Mixue Ice Cream 🍦<br>
        如果喜欢本项目，可以送作者一杯蜜雪冰城🍦<br>
        Your support is the motivation for the author to develop and maintain the project 🚀<br>
        您的支持就是作者开发和维护项目的动力🚀
      </p>
      <div class="about-donations">
        <figure><img :src="'/static/gui/donate/wechat.png'" alt="WeChat Pay"><figcaption>WeChat Pay (微信)</figcaption></figure>
        <figure><img :src="'/static/gui/donate/alipay.png'" alt="Alipay"><figcaption>Alipay (支付宝)</figcaption></figure>
        <figure><img :src="'/static/gui/donate/alipayremit.png'" alt="Alipay Remit"><figcaption>Alipay Remit (For international)</figcaption></figure>
      </div>
      <div class="about-disclaimer">
        <h3>⚠️ Disclaimer / 使用风险与免责声明</h3>
        <p>
          NKAS is an automation tool that works by screen capture and simulated input. Using such tools may violate the game's Terms of Service and could result in account penalties, including but not limited to warnings, restrictions or bans. By using NKAS you acknowledge and accept these risks; the authors assume no responsibility for any loss arising from the use of this software.<br>
          NKAS 是一款基于截图与模拟输入的自动化工具。使用此类工具可能违反游戏用户协议，可能导致账号受到处罚（包括但不限于警告、限制或封禁）。使用 NKAS 即表示您已知晓并自行承担上述风险；因使用本软件造成的任何损失，作者概不负责。
        </p>
      </div>
    </article>
  </section>
</template>
