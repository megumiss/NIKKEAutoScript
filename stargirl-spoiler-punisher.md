   ┌ plan: stargirl-spoiler-punisher.md ──────────────────────────────────────────────────────────────────────────┐
   │ 总览页（DashboardView）卡片化布局改造                                                                        │
   │                                                                                                              │
   │ 目标页面                                                                                                     │
   │                                                                                                              │
   │ webui/src/views/DashboardView.vue（路由 /，侧栏"总览"，非实例的"任务总览"）。                                │
   │                                                                                                              │
   │ 现状                                                                                                         │
   │                                                                                                              │
   │ 页面自上而下三个大内容区域：                                                                                 │
   │ 1. .stat-row 统计卡片（实例总数 / 运行中）                                                                   │
   │ 2. .inst-grid 实例卡片区（含"新建实例"按钮）                                                                 │
   │ 3. EventCalendar 活动日历                                                                                    │
   │                                                                                                              │
   │ 需求                                                                                                         │
   │                                                                                                              │
   │ • 三个区域支持拖拽换位、调整大小（grid-layout-plus 网格布局，配置存 localStorage）。                         │
   │ • 按实例新增“实例概览”卡片：卡片内部固定分为账号信息与本日任务两栏；两栏不独立拖拽，保持实例维度绑定。       │
   │     • 账号信息：指挥官等级、爬塔进度等统计（本次只做卡片外壳与占位字段 --，数据源后接）。                    │
   │     • 本日任务：企业塔是否失败、竞技场胜/负场次、今日获得石头等（占位 --，注明每日 04:00 清零，数据源后接）  │
   │       。                                                                                                     │
   │                                                                                                              │
   │ 已确认的决策                                                                                                 │
   │                                                                                                              │
   │ • 实现：grid-layout-plus（vue-grid-layout 的 Vue 3 维护中 fork，API 相同，有稳定版；vue-grid-layout 本身的   │
   │   Vue 3 支持只停在 3.0.0-beta1，多年未更新）。                                                               │
   │ • 布局存储：localStorage，键 nkas-dashboard-layout-v3（全局）。                                              │
   │ • 粒度：stats、instances、每个实例概览、calendar 各自独立可拖拽/缩放；账号信息/本日任务为组内固定分栏。      │
   │                                                                                                              │
   │ 改动清单                                                                                                     │
   │                                                                                                              │
   │ 1. 依赖                                                                                                      │
   │                                                                                                              │
   │ • webui/ 下 yarn add grid-layout-plus（更新 package.json + lockfile），并引入其样式文件                      │
   │   grid-layout-plus/dist/style.css（拖拽占位、缩放手柄）。                                                    │
   │                                                                                                              │
   │ 2. 新增 webui/src/stores/dashboardLayout.ts                                                                  │
   │                                                                                                              │
   │ • 区域注册表：固定区域 stats、instances、calendar；按实例动态生成 instance:<实例名>。各带默认 x/y/w/h 与     │
   │   minW/minH（12 列网格，rowHeight 约 34px，横向间距 18px、编辑态纵向间距 0）。默认布局顺序为 stats 顶部通栏、instances 次之、每个 │
   │   实例概览卡片按行堆叠，calendar 始终在最下面通栏；实例概览卡片内部为账号信息（左半）+ 本日任务（右半）。 │
   │ • 多实例布局协调：加载时与当前实例列表对账——新实例的卡片自动按上述规则追加到底部，已删除实例的卡片从布局中剔 │
   │   除；整个映射持久化。                                                                                       │
   │ • 状态：editing（编辑模式，开启才可拖拽/缩放）、layout。                                                     │
   │ • 动作：toggleEdit()、resetLayout()、saveLayout()（grid @layout-updated 时写 localStorage）。                │
   │ • 窄屏（≤1200px，跟随 base.css 现有断点）降级为单列纵向堆叠并禁用编辑。                                      │
   │                                                                                                              │
   │ 3. 改造 webui/src/views/DashboardView.vue                                                                    │
   │                                                                                                              │
   │ • 普通态使用自然纵向卡片流，保持旧页面的外侧布局和适度卡片间距；编辑态切换为 GridItem 拖拽/缩放。默认内容全部展开。 │
   │ • 删除实例区末尾的「＋ 新建实例」空白卡片（add-card 按钮及不再使用的 openCreateModal 导入；新建入口保留多开  │
   │   页 ManageView 的「＋ 新建实例」按钮）。.add-card 相关 CSS（base.css 第 24、418-419 行）仅此一处使用，一并  │
   │   删除。                                                                                                     │
   │ • 按实例 v-for 渲染实例概览 GridItem（key 为 instance:<name>），卡片内部并排渲染账号信息/本日任务。          │
   │ • 区块顶部加小工具条：编辑布局/完成 切换按钮 + 重置布局按钮；文案走 t()，i18n.ts 补充词条（账号信息、本日任  │
   │   务、编辑布局、重置布局等）。                                                                               │
   │ • 引入 grid-layout-plus 所需样式（拖拽占位、缩放手柄，随 dist/style.css 引入）。                             │
   │                                                                                                              │
   │ 4. 新增占位卡片组件（按实例渲染，props 传实例名，标题带实例名前缀）                                          │
   │                                                                                                              │
   │ • webui/src/components/AccountInfoCard.vue：标题"{实例名} · 账号信息"，行式字段（指挥官等级、无限之塔、企业  │
   │   塔进度等）显示 --，底部小字"数据待接入"。                                                                  │
   │ • webui/src/components/DailyTasksCard.vue：标题"{实例名} · 本日任务"，行式字段（企业塔结果、竞技场胜/负、今  │
   │   日获得石头）显示 --，注明"每日 04:00 清零 / 数据待接入"。                                                  │
   │ • 复用现有 .card 视觉规范，新样式写组件 scoped 块。                                                          │
   │                                                                                                              │
   │ 5. 样式 webui/src/styles/base.css                                                                            │
   │                                                                                                              │
   │ • 新增总览页网格容器与 GridItem 内卡片填充规则（卡片 100% 高、内容默认展开）、编辑模式拖拽光标与缩放手柄样式； │
   │   保留原 .stat-row/.inst-grid 内部样式作为卡片内容。                                                         │
   │                                                                                                              │
   │ 6. 验证                                                                                                      │
   │                                                                                                              │
   │ • webui/ 下 yarn run typecheck、yarn run build（webui/dist 入库，产物一并更新）。                            │
   │ • 检查点：默认布局与现视觉一致；编辑模式可拖拽/缩放且刷新后保持；重置恢复默认；窄屏单列堆叠；活动日历标题不重复； │
   │   卡片纵向无额外空白；实例概览为账号信息/本日任务双栏信息面板。                                               │
   │                                                                                                              │
   │ 不做                                                                                                         │
   │                                                                                                              │
   │ • 不改动实例"任务总览"页（OverviewView）。                                                                   │
   │ • 不接数据源，账号信息/本日任务仅占位。
