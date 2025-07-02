<div align="center">

<img alt="LOGO" src="./webapp/src/assets/img/Helm.png" width="256" height="256" />

# NIKKEAutoScript

胜利女神：NIKKE 自动日常脚本，支持除**国服**外的所有客户端（大概）。Fork自[NIKKEAutoScript](https://github.com/takagisanmie/NIKKEAutoScript)

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blueviolet">
  <img alt="license" src="https://img.shields.io/github/license/megumiss/NIKKEAutoScript">
  <br/>
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/megumiss/NIKKEAutoScript">
  <img alt="stars" src="https://img.shields.io/github/stars/megumiss/NIKKEAutoScript?style=social">
</p>

</div>

---

### 鸣谢

Alas的所有开发者，到现在我还是觉得Alas的源码不明觉厉  
让我学到了，"草，Python还能这么写？"  
然后想到了自己写的最初版代码，那写的是什么shit😅😅😅  
虽然现在写的NKAS依旧是shit😅😅😅

---

### 联合

其他平台/方式的脚本：
- [DoroHelper](https://github.com/1204244136/DoroHelper)（PC端+AutoHotkey）
- [autoxjs-scripts](https://github.com/Zebartin/autoxjs-scripts)（安卓端+autojs）
- [MNA](https://github.com/MAA-Nikke/MNA)（安卓端+MaaFramework）

其他工具：
- [Nikke-CDK-Tool](https://github.com/Small-tailqwq/Nikke-CDK-Tool)（CDK收集/兑换）

---

### 如何使用

#### 需要一个可正常运行nikke的安卓"模拟器"，并且可以使用adb远程连接，比如：云手机、ARM架构模拟器、ARM架构的Redroid/Waydroid

#### Windows
0. ~~可以加入qq划水群 823265807~~  
1. 将设备设置为竖屏，分辨率`720*1280`，240DPI  
2. 运行`updater.bat`(一般要开代理)，打开`app\NikkeAutoScript.exe`，在脚本设置中将`Serial`改为设备的adb连接地址，比如`127.0.0.1:16384`  
3. 调整画质设置，参考常见问题最后一个(`光晕效果`和`颜色分级`一定要打开)，然后可以运行脚本了  

#### Linux
1. `git clone https://github.com/megumiss/NIKKEAutoScript.git`  
2. `cd NIKKEAutoScript && cp config/deploy.template-docker.yaml config/deploy.yaml`  
3. `docker compose up -d`  
4. 浏览器打开`127.0.0.1:12271`  

---

### 更新计划
- [ ] 冠军竞技场
- [ ] 大型活动的小游戏、商店购买
- [ ] 个突
- [ ] 模拟室快速
- [ ] 半自动推图
- [ ] 废铁商店优化，支持骨头货币
- [x] 通知优化
- [x] 支持自动更新、定时更新
- [x] 支持docker部署

### 支持功能
- [x] 每日收获、歼灭、派遣
- [x] 友情点、特殊竞技场点数领取
- [x] 每日、周、月免费钻领取
- [x] 普通商店、竞技场商店、废铁商店
- [x] 每日企业塔，自动普通企业塔
- [x] 异常拦截（只支持克拉肯），支持自动打红圈
- [x] 自动模拟室
- [x] 协同作战普通摆烂
- [x] 每日咨询、送礼
- [x] 普通竞技场、特殊竞技场自动战斗
- [x] 大型活动扫荡、挑战、签到、奖励
- [x] 小型活动扫荡、挑战、奖励（待测试）
- [x] 活动免费、友情点每日抽卡
- [x] 邮箱领取
- [x] 排名奖励领取
- [x] 每日、每周任务奖励
- [x] 每日pass领取
- [x] blablalink社区每日任务，CDK自动兑换

---

### 常见问题

#### Q1：运行NikkeAutoScript.exe提示'Failed to load resource'
**A1**：可能是运行`updater.bat`时，没有完全更新成功，可以尝试重新解压后再次运行`updater.bat`

#### Q2：主程序没有内容，只有顶部栏
**A2**：可能是缺失了某个依赖，可以通过CMD手动安装requirements.txt中的依赖：  
`./toolkit/python.exe -m pip install -r requirements.txt --disable-pip-version-check`

#### Q3：updater.bat在运行后一闪而过
**A3**：这应该是无法访问Github时会出现的情况，可以在`config/deploy.yaml`的GitProxy字段中填写代理，或者更改hosts等

#### Q4：在咨询任务中，无法咨询任何NIKKE
**A4**：请检查NIKKE的画质设置，确保选项拥有'光晕效果'和'颜色分级'两个选项，以及在游戏中收藏了想咨询的NIKKE

#### Q5：在运行某个任务时，没有正确点击
**A5**：可能是模拟器的分辨率为`1280 * 720`，但在运行NIKKE时是以竖屏运行的，这样会导致点击到错误位置，请设置为`720 * 1280`后，再次尝试

#### Q6：更新界面只有None
**A6**：这是因为没有初始化Git，请运行`updater.bat`

#### Q7：主程序更新时，一直在转圈圈
**A7**：同A3

#### Q8：运行调度器时出现'BaseError: /data/app/com.proximabeta.nikke-.......apk: no such file or directory'
**A8**：这是因为模拟器的NIKKE是通过Google Play下载的，可以卸载后在QooApp下载，可以解决这个问题

#### Q9：进入付费商店时，提示'网络异常，读取商店结账资讯失败'
**A9**：这很可能是因为模拟器的Google Play未登录账号，可以登录账号后再尝试

---

### 推荐画面设置

![推荐画面设置](https://raw.githubusercontent.com/megumiss/NIKKEAutoScript/master/doc/assets/setting.png)

---

### GUI预览

![GUI预览](https://raw.githubusercontent.com/megumiss/NIKKEAutoScript/master/doc/assets/gui.png)

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=megumiss/NIKKEAutoScript&type=Timeline)](https://www.star-history.com/#megumiss/NIKKEAutoScript&Timeline)
