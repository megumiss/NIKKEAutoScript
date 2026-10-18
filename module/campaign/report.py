"""推图日志的用词表：把内部标识翻成中文，让日志不读代码也能看懂。

只做翻译、不改判断：阈值、优先级与状态机仍在各自模块里。同一个标识在所有行里写同一个说法
（例如定位状态 accepted 一律写「已确认」），查不到的词原样输出，新增标识不会变成「未知」而丢信息。
内部编号（enemy-3、block-1、cell-15-44 之类）一律保留原样：中文让人看懂，编号让人能在日志里
检索到同一个目标。
"""

import numpy as np

# 目标类型；日志里写成「普通敌人 enemy-1」这种「中文名 + 内部编号」。
GOAL_KINDS = {'enemy': '普通敌人', 'collectible': '收集品', 'explore': '未探索区域',
              'review_block': '复核光幕', 'mechanism': '地面机关', 'elevator': '电梯', 'none': '无目标'}
# 选择这个目标的依据。
GOAL_REASONS = {
    'nearest_pending_enemy': '可达待清敌人中道路距离最短',
    'nearest_annotated_collectible': '候选标注收集品中道路距离最短',
    'nearest_unobserved_cell': '最近的可达未探索区域',
    'review_blocking_curtain': '复核挡路的光幕',
    'verify_mechanism_curtain': '机关触发后复核光幕状态',
    'try_mechanism_for_curtain': '尝试用机关解除挡路光幕',
    'transfer_to_pending_target': '经电梯前往当前不可达的目标',
    'localization_unavailable': '章节地图定位不可用，坐标不可信',
    'coordinate_model_unsupported': '本章地图的坐标模型不支持规划',
    'curtain_review_unavailable': '无可用光幕复核位置',
    'blocked_by_curtain': '光幕阻断路径，且无可用机关或复核位置',
    'no_reachable_target': '没有可达的目标',
}
# 本轮排除某个候选的原因。
REJECT_REASONS = {'different_component': '不在同一片可达区域', 'no_road_route': '没有可走的道路',
                  'beyond_candidate_budget': '排在候选预算之外'}
# 一帧观测的可信度。
LOCALIZATION = {'accepted': '已确认', 'reused': '沿用上一次定位', 'needs_review': '待复核',
                'unavailable': '不可用'}
MINIMAP_MODES = {'compact': '紧凑', 'expanded': '展开'}
# 光幕封锁的三态。
BLOCK_STATUS = {'active': '仍存在', 'stale': '待确认', 'cleared': '已解除'}
# 运行状态摘要里的敌人状态。
ENEMY_STATUS = {'pending': '待清', 'cleared': '已清除', 'engaged': '交战中', 'unreachable': '暂时不可达'}
# 共享移动闭环的进度状态（navigation/*.py 的 emit 值）。
MOVE_STATES = {'locating': '定位小队', 'planning': '规划落点', 'moving': '小队移动中',
               'panning': '平移画面', 'probing': '试探附近落点', 'verifying': '复核到点'}
# 落点是怎么算出来的。
NAVIGATION_METHODS = {'squad_anchor': '小队相对位置投影', 'compact_gaze': '镜头注视点点击',
                      'camera_pan': '镜头平移后点击', 'direct_target': '目标投影点点击',
                      'arrow_wake': '小队箭头唤醒', 'enemy_ring': '两帧脚圈匹配点击'}
# 镜头位置是怎么跟踪的。
CAMERA_TRACKING = {'predicted': '按预测位置', 'minimap': '小地图校正', 'chained': '帧间跟踪',
                   'accepted': '全局对齐', 'needs_review': '全局对齐（待复核）', 'fused': '容差对齐',
                   'expanded': '展开地图重新锚定'}
# 提前结束一批平移的原因。
CORRECTION_REASONS = {'prediction_failed': '预测失效', 'no_scene_motion': '画面未产生位移',
                      'pan_budget': '达到平移次数上限', 'batch_limit': '达到单批平移次数上限',
                      'relative_target_reached': '进入相对目标容差范围'}
# 批末用展开地图重新校正镜头位置的结果。
ANCHOR_RESULTS = {'accepted': '已采用', 'rejected': '校正位移超限，未采用', 'failed': '失败'}
# 视野坐标指的是谁。
POSITION_KINDS = {'squad': '小队坐标', 'viewport': '视野中心', 'predicted_viewport': '预测的视野中心'}
# 机关坐标的来源。
TARGET_SOURCES = {'annotation': '地图标注', 'scene_fallback': '场景识别兜底'}
# 小队箭头锚点的取法。
ANCHOR_METHODS = {'arrow_cycle': '箭头周期采样', 'minimap_ground_projection': '小地图地面投影'}
# 移动结果的终态。
RESULT_STATES = {'arrived': '已到点', 'needs_review': '已到附近但未确认触发'}
# 战斗接管的画面状态与最终结果。
BATTLE_STATES = {'entry': '战斗准备弹窗', 'battle': '战斗中', 'victory': '胜利结算',
                 'defeat': '战败结算', 'field': '野外画面', 'unknown': '未识别'}
BATTLE_OUTCOMES = {'victory': '胜利', 'defeat': '战败', None: '未确认结算'}
# 任务提前停止时附带的证据种类。
EVIDENCE_KINDS = {'battle_popup': '画面出现战斗准备弹窗', 'ex_stage_skipped': '画面出现 EX 关卡弹窗（仅关闭，不接管）',
                  'collectible_indicator': '画面出现可拾取的收集品提示'}
# 章节地图定位未通过时，观察器给出的未达标项。
LOCATE_CRITERIA = {'road_iou': '道路重合度', 'coarse_margin': '候选位置分差',
                   'parameter_spread': '参数离散度'}
# 展开地图里的平移方向。
DIRECTION_NAMES = {(1, 0): '向右', (-1, 0): '向左', (0, 1): '向下', (0, -1): '向上'}


def label(table, value):
    """按词表翻译一个标识；词表里没有的原样返回，保证新标识仍能在日志里看到。"""
    return table.get(value, value)


def points_text(value):
    """把二维点或点列压成一位小数坐标：单点写成 (x, y)，点列写成 [(x, y), …]。"""
    points = np.asarray(value, float)
    if points.ndim == 1:
        return '(' + ', '.join(f'{number:.1f}' for number in points) + ')'
    return '[' + ', '.join('(' + ', '.join(f'{number:.1f}' for number in point) + ')' for point in points) + ']'


def counts_text(counts):
    """把「说法 → 个数」压成「待清 2、已打掉 1」；一条记录都没有时写「无」。"""
    return '、'.join(f'{name} {count}' for name, count in counts.items()) or '无'


def direction_text(direction):
    """把客户区方向向量写成「向右」这类说法；认不出的（斜向、带缩放）按原值输出。"""
    return label(DIRECTION_NAMES, tuple(int(value) for value in direction))
