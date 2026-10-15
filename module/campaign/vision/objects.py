"""客户区与小地图里的目标识别：把 YOLO 类别统一成后续推图逻辑使用的语义对象。"""

from module.campaign.vision.detection import GRID_LABELS, LEGACY_SCENE_LABELS, MINIMAP_LABELS, detect_grid, detect_minimap, detect_scene

# 模型类别到语义的映射；state 区分同一类目标的状态或形态，后续逻辑按 kind 取、按 state 判断。
OBJECTS = {
    'scene_squad_arrow': ('squad_arrow', None),
    'scene_collectible_indicator': ('collectible_indicator', None),
    'scene_ground_mechanism_off': ('ground_mechanism', 'off'),
    'scene_ground_mechanism_on': ('ground_mechanism', 'on'),
    'scene_elevator_start': ('elevator', 'start'),
    'scene_elevator_end': ('elevator', 'end'),
    'scene_barrier_curtain': ('barrier', 'curtain'),
    'scene_barrier_pillar': ('barrier', 'pillar'),
    'minimap_enemy_normal': ('enemy', 'normal'),
    'minimap_enemy_ex': ('enemy', 'ex'),
    'minimap_squad_ring': ('squad_ring', None),
}
# 整幅画面网格检测的语义类别，也是 detect_objects 的默认范围。
GRID_KINDS = ('ground_mechanism', 'elevator', 'barrier')
SCENE_KINDS = GRID_KINDS + ('squad_arrow', 'collectible_indicator')
MINIMAP_KINDS = ('enemy', 'squad_ring')


def requested(kinds, default):
    """统一校验语义类别参数；单个字符串会被逐字符当成类别，必须先挡掉。"""
    if kinds is None:
        return default
    if isinstance(kinds, str):
        raise ValueError('kinds 需要语义类别的序列，而不是单个字符串。')
    return tuple(kinds)


def kind_labels(kinds, available):
    """把语义类别换成模型类别；未知类别或取图方式不匹配时报错，不静默忽略。"""
    names = {OBJECTS[label][0] for label in available}
    selected = requested(kinds, ())
    unknown = [kind for kind in selected if kind not in names]
    if unknown:
        raise ValueError(f'这些目标类别不能用当前取图方式检测：{", ".join(map(str, unknown))}。')
    return tuple(label for label in available if OBJECTS[label][0] in selected)


def described(detection, space):
    """把单个模型检测结果整理成后续逻辑使用的语义对象。

    space 记录坐标系（client 为客户区像素、minimap 为面板像素），两者不能混用；坐标保留一位
    小数、置信度保留四位，避免日志和快照里出现无意义的长浮点数。
    """
    kind, state = OBJECTS[detection.label]
    return dict(label=detection.label, kind=kind, state=state, confidence=round(detection.confidence, 4),
                position=[round(float(value), 1) for value in detection.center],
                box=[round(float(value), 1) for value in detection.box], space=space)


def detect_objects(image, kinds=None):
    """1776×999 BGR 客户区截图里的场景目标，坐标为客户区像素。

    默认只检测机关、电梯和屏障，整幅画面切八片推理。小队箭头和收集提示另有已验证的遮罩
    切片取图方式，只有显式请求时才多跑一遍；箭头的精确中心仍应使用 perception.squad_arrow。
    """
    selected = requested(kinds, GRID_KINDS)
    grid = kind_labels([kind for kind in selected if kind in GRID_KINDS], GRID_LABELS)
    legacy = kind_labels([kind for kind in selected if kind not in GRID_KINDS], LEGACY_SCENE_LABELS)
    detections = list(detect_grid(image, grid)) if grid else []
    if legacy:
        detections += detect_scene(image, legacy)
    return sorted((described(item, 'client') for item in detections),
                  key=lambda item: item['confidence'], reverse=True)


def detect_minimap_objects(panel, kinds=None):
    """小地图面板里的小队圆环与敌人标记，坐标为面板像素。

    panel 由调用方按展开或紧凑模式裁好；圆环与普通敌人的精确中心仍应使用 perception 中
    对应的精定位，这里只给出检测框中心。
    """
    labels = kind_labels(requested(kinds, MINIMAP_KINDS), MINIMAP_LABELS)
    return sorted((described(item, 'minimap') for item in detect_minimap(panel, labels)),
                  key=lambda item: item['confidence'], reverse=True)
