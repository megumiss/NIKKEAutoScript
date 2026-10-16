# 自动推图流程设计

本文是 `auto_main_story` / `auto_main_collectible` 两个任务（入口 `module/campaign/campaign.py` 的
`Campaign.run`）的推图业务设计。地图数据与定位原理见[地图数据原理](minimap-wiki-principles.md)，
移动闭环与坐标换算见[原型导航原理](campaign-prototype-principles.md)，标注格式见[地图标注说明](map-annotation.md)。

## 1. 地图信息的三层

| 层 | 位置 | 生命周期 |
| --- | --- | --- |
| 静态层 | `module/campaign/map/map_state.py` 的 `StaticMap` | 按章节加载一次：道路掩码、`effective` 连通分区、点状标注、电梯传送关系 |
| 观测层 | `module/campaign/vision/observation.py` 的 `CampaignObserver.observe` | 每帧整体替换：小地图形态与标记、底图定位、场景目标、屏障、离屏标记 |
| 运行状态层 | `module/campaign/run_state.py` 的 `RunState` | 按章节＋难度一份，跨帧累积：敌人、机关、封锁、已观测区域、冷却 |

三层已接入。正式任务在准备完成后进入有限执行闭环；空跑入口继续保留，供不发送输入的决策诊断。

## 2. 为什么必须有运行状态层

观测是个钥匙孔，单帧信息不足以决策：

- **紧凑小地图只覆盖小队附近一小块。** 实测一帧（38 章）的面板四角投影到底图是
  TL(-168, 992)、TR(540, 1004)、BL(50, 1561)、BR(332, 1560)，小队在 (185, 1395)：
  前方只看到约 400 底图像素，身后只有约 165。光幕后面的近敌根本不在窗口里，
  窗口里反而是更靠后那只，于是「本帧最近的可见敌人」会直接选错。
- **敌人只有小地图类别。** 模型的十一类里没有场景敌人（见 `module/campaign/vision/objects.py` 的 `OBJECTS`），
  所以画面里站着的敌人不进检测，只能靠小地图标记「看见」。
- **快照每帧整体替换。** 机关踩过没有、敌人打掉没有、光幕还在不在，观测层一律不记。
  「踩完机关回主路」这类事实，靠任何单帧都推不出来。

单帧观测成本不是瓶颈：热态 0.70s（八片网格 0.20s、离屏 0.03s、配准约 0.45s），每 tick 跑全套可接受。

## 3. 运行状态层 RunState

| 集合 | 内容 | 更新规则 |
| --- | --- | --- |
| `enemies` | 底图坐标、kind、首见／末见 tick、状态（pending／engaged／unreachable）、失败次数 | 小地图标记每帧重检，按 ≤25 底图像素聚类合并到既有项 |
| `mechanisms` | 标注 id、底图坐标、`tried_for[block_id]` | 只记「为哪块封锁、第几 tick 踩过」，不记开关状态 |
| `blocks` | `StaticMap.mark_barrier` 给出的切口段、状态（active／stale／cleared） | 只在紧凑帧更新；判据见下节 |
| `observed` | 已被小地图覆盖过的 32px 粗网格 | 每帧把面板四角投影出的四边形打到网格上，给出前沿 |
| `cooldowns` | 目标 id → 失败次数与冷却 tick | 失败冷却五个决策拍，累计三次失败后本次任务不再选；移动反馈帧与同一拍内的补视野、复核帧不推进拍数 |
| `pending_reviews` | 封锁 id → 本次踩过的机关 id | 到机关后先回封锁口复核，没进入可靠视野时保留 |
| `transfers` | 有向电梯航段 → 失败次数与冷却 tick | 失败冷却五拍，三次失败后本次任务禁用该方向；不封掉反向航段 |

## 4. 光幕：唯一以 YOLO 为主判据的目标

光幕是动态的，没有标注可依，所以它的存在与消失完全由 `scene_barrier_curtain` 检测决定。
落图走 `module/campaign/vision/barriers.py` 的 `scene_chart` 投影链（箭头锚点实测中位约 6、p90 约 16 底图像素）。

| 状态 | 含义 | 迁移条件 |
| --- | --- | --- |
| `active` | 最近一次它在视野内时还在 | 检到 curtain → 投影 → `mark_barrier` 切路网 |
| `stale` | 记录过，之后没再进过视野 | 规划要穿过它时先去复核 |
| `cleared` | 它该出现在视野里却没检到 | 解除切口、重算分区 |

两条约束：

- **只在紧凑帧更新。** 展开面板的投影链与 `scene_chart` 的标定不同源，展开帧落点会整体偏。
  `barriers_observed` 还须为真才允许判消失：场景地面锚点或投影失败时只保留 stale，不能把空结果当解封。
  场景识别失败、或检到的光幕有一块没吸附上道路时同样不置真——缺项只说明没落图，不说明光幕不在。
- **复核要站在能看见它的地方。** 复核站位取封锁段中点沿道路回退一段的安全点，
  不是光幕本体——光幕必须进紧凑窗口才能判定「没了」。到站后最多连看三帧，都不可信才记一次复核失败。

### 屏障进路网

派生视图「切开路网」（`module/campaign/navigation/routing.py` 的 `CutRoads`）＝`road ∧ ¬(挡路封锁的切口)`，
按封锁集合的签名缓存，集合变了才重算分区。挡路封锁取 active 与 stale 两种：stale 是「记过但
之后没再看过」，规划阶段宁可当它还在，等复核过再解除，否则会把小队送去撞光幕。
可达性与 A\* 全部走切开后的图；原图保留不动，于是「解封之后能不能走」是一次现成的查询。
切口几何与人工分隔线同源（`connectivity.build` 的 effective 分支：3×3 开运算 + 按固定宽度画实线
+ 八邻域连通 + 抽到 8 像素网格），所以派生分区与地图包里存档的分区是同一套规则算出来的。

**目标在原图可达、在切开图不可达 ＝ 被光幕挡住**。拦路的是哪一块用「逐个撤销再试可达」试出来，
然后按它的状态分流：

| 拦路封锁状态 | 动作 |
| --- | --- |
| `stale` | 去复核站位看一眼（目标类型 `review_block`） |
| `active` | 不复核——刚看到它还在，再看一眼没有新信息，而且站位就在封锁旁会自锁成反复往同一处走；该走机关解封 |

## 5. 机关：标注为准，踩下去是原子操作

- **位置**来自标注的 `ground_mechanism` 点；
- **触发**就是走到该点（`navigate(purpose='position')`，到达半径 `SQUAD_REACH` = 28 底图像素），
  踩上即视为已踩，**不做 on/off 确认**——YOLO 的机关类别只在标注缺失时兜底给位置，不作为状态判据；
- **验证只看光幕**：回到封锁口让光幕位置进紧凑视野，没了就把 block 标 `cleared` 并重算路网；
  还在就把这个机关记为「对这块封锁无效」，试下一个。

这样机关与光幕不需要配对标注，也不依赖 YOLO 的置信度。标注还没补的章节（38 章当前就是，
17 个点全是旧 Wiki 收集品）用场景检测落图当临时目标，日志里标明来源是兜底。

## 6. 电梯：标注给位置，状态靠乐观尝试

- 路线用 `StaticMap.transfers`（读标注的 `elevator_connection`，含 `directed`），**只走 entry→exit**，
  终点不当入口；
- 看得见时用 `scene_elevator_start` 当状态提示：标注点附近检到起点 → 判这条航段现在可用；
- 被遮挡或检不到就乐观尝试：走到 entry 等传送，用 `module/campaign/navigation/elevator_navigation.py`
  的 `at_elevator_exit` 连续两次确认出口才算传送成功；在 entry 停稳等待若干次没反应 →
  该航段标 `unavailable` 并冷却，重新规划绕路。

`plan_elevators` 与 `navigate` 复用同一套骨架。目标选择先过滤冷却或禁用方向，执行器只派出第一段，
确认出口后按新快照重选目标。航段失败回写 `RunState`；只有 `elevator_transfers` 的实际出口确认才能清掉失败记录。

## 7. 视野策略

- 常规 tick 用紧凑面板：便宜、移动闭环的假设就是它、屏障也只能在它上面投；
- 展开一次（展开→观测→收起）只在四种情况：没有 pending 目标、选中目标的路径穿过未观测区、
  战斗刚结束、刚解封。展开面板面积是紧凑的数倍，一次能把更靠后的敌人和附近路网一起记下来；
- 目标排序永远用 `RunState` 里记住的集合加切开图的行走距离，不用「本帧最近」。

## 8. 目标优先级

一个 tick 一个决策。战斗接管尚未接入，所以本期敌人只走到弹窗为止：

```
0 发现战斗弹窗 → 按既有契约处理（普通保留 / EX 关掉一次）→ 结束本次任务并上报
1 切开图上最近的 pending 敌人 → 走到它并停在弹窗
2 踩过机关的封锁先回安全站位复核；最优目标仅因光幕不可达 → stale 复核，active 试同侧未尝试机关
3 必须换层或跨区 → 电梯航段
4 前沿探索（展开一次 + 朝本分量最近未观测格移动）
5 没有可做的 → 报「无进展」交人工
```

## 9. 执行层

移动闭环（`module/campaign/navigation/camera_navigation.py` 的 `plan_world_move_compact` 与
`module/campaign/navigation/movement.py` 的 `navigate`，含注视点居中、响应增益闭环、停稳检测、
停滞与重规划、箭头恢复）已经现场验证过，但它是按采集器那套受控窗口写的，不是任务调度的 `Device`。

地图、视觉与移动代码统一在 `module/campaign/`，按 `map`、`vision`、`navigation`、`tools` 分目录。
接入方式是**写适配器**，分两层，只保留一份移动实现：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 受控窗口 | `module/campaign/window.py` 的 `CampaignWindow` | 把 `Device` 包成闭环要的 `check` / `capture` / `focus` / `hwnd` / `gui` / `handler` / `reset_minimap`；客户区原点、尺寸与前台任一变化即抛 `WindowChanged`，不让点击发到错位坐标上 |
| 会话 | `module/campaign/session.py` 的 `CampaignSession` | 补齐 `observe` / `move` / `localizer`，继承 `navigation.session.NavigationSession` 的共享 `plan` / `wait_stopped` / `finish_view`；生产截图使用 `Device`，移动反馈也更新敌人与封锁 |

`module/campaign/execution.py` 的 `CampaignExecutor` 负责补视野、派出目标与回写结果；
`module/campaign/subgoals.py` 负责机关和有向电梯的纯规划。
剧情任务选择普通敌人，不主动选择 EX；收集品任务选择当前难度的点状标注，发现橙色提示即停，不追加拾取点击。
普通战斗弹窗保留，EX 弹窗只关闭一次，任何目标用途遇到战斗弹窗都结束本次任务。

## 10. 护栏

- 每章最多 120 次输入，单目标最多 32 次输入（含镜头手势与小地图开合），每次任务最多 48 轮；
  单目标最多 12 次移动点击，停滞检测沿用 `navigate` 的两次无位移即停；
- 准备阶段先跑 `Campaign.check_main_story_page`；执行期间每次新输入前核对章节与难度，
  身份不明、窗口原点／句柄／尺寸变化、失焦或停止请求一律禁止后续输入；等待阶段也检查停止与窗口；
- 战斗结果判不出来就不猜，交人工；
- 每个决策打一行日志：目标 id、类型、距离、为什么选它；失败时发预览帧留证据。

## 11. 分期

| 期 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 生产代码不再反向依赖 `dev_tools`：地图库搬进 `module/campaign/` | 已完成 |
| 2 | `RunState` + tick 骨架 + dry-run（只观测只打日志不点击），用现场帧回放校验决策 | 已完成 |
| 3 | 光幕进路网：派生切开路网视图 + 三态复核 | 已完成 |
| 4 | 会话适配器：生产 `Device` 上跑通单目标到点，41 章现场验证 | 代码与离线回归完成；41 章实机验收待做 |
| 5 | 解封子目标端到端（38 章，等机关标注）、电梯航段失败回写、前沿探索 | 代码与离线回归完成；标注补齐与实机验收待做 |
| — | 横屏战斗接管（自动射击、结算、胜负判定的横屏模板） | 本期跳过 |

生产入口及共享闭环导入时均不加载 `dev_tools` 或 `module.campaign.tools`。采集器窗口单独留在
`module/campaign/tools/window.py`，独立实验命令访问 `runtime.Window` 时才延迟加载。
运行状态层、切开路网和目标选择分别在 `run_state.py`、`navigation/routing.py`、`goals.py`，
`Campaign.plan_tick` 与 `Campaign.dry_run` 可独立诊断决策，空跑不发送输入。

### 离线验证与剩余验收

- `tests/test_campaign_execution.py` 覆盖生产入口接线、真实 A* 的切口可达性、机关尝试／复核、
  有向电梯失败与备用路线、共享导航的连续到点及出口确认、探索补视野、取消／失焦／窗口变化、
  输入预算、普通／EX 弹窗终态与驱动释放。假设备不访问真实窗口，不发送真实输入。
- 2026-10-15 检查当前资源包：第 38、41、48 章可以加载，但只有收集品点，没有机关点与电梯连接。
  机关可用可信紧凑帧投影作临时兜底；电梯必须补齐点状入口／出口及传送关系，不凭场景检测猜连接。
- 第 41 章单目标移动、第 38 章踩机关后光幕解除、补标注后的电梯传送与失败绕行、探索停稳，
  仍须按[现场验证路径](map-annotation.md#生产任务验收路径)在游戏内验收。
- 中文客户端及其他语言的章节／难度 OCR、普通／EX 弹窗都没有在本次工作中做实机复测；
  离线回归不代表游戏内通关，也不代表战斗或拾取成功。

离线回归（仓库根目录）：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_campaign_execution.py -v
```
