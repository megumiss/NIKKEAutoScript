"""客户区与小地图里的目标识别：把 YOLO 类别统一成后续推图逻辑使用的语义对象。"""

from module.campaign.vision.detection import SCENE_LABELS, MINIMAP_LABELS, detect_minimap, detect_scene

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
    'scene_enemy_ring': ('enemy_ring', None),
    'minimap_enemy_normal': ('enemy', 'normal'),
    'minimap_enemy_ex': ('enemy', 'ex'),
    'minimap_enemy_boss': ('enemy', 'boss'),
    'minimap_squad_ring': ('squad_ring', None),
}
# 默认返回导航对象；箭头和收集提示复用原图的场景推理，需要时再筛选。
DEFAULT_SCENE_KINDS = ('ground_mechanism', 'elevator', 'barrier', 'enemy_ring')
SCENE_KINDS = DEFAULT_SCENE_KINDS + ('squad_arrow', 'collectible_indicator')
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

    默认返回机关、电梯、屏障和敌人脚圈；光幕使用灰度增强输入，其余类别共享原图推理。
    显式请求箭头或收集提示只需原图输出；脚圈不直接并入敌人记忆。
    箭头的精确中心仍应使用 perception.squad_arrow。
    """
    selected = requested(kinds, DEFAULT_SCENE_KINDS)
    labels = kind_labels(selected, SCENE_LABELS)
    detections = detect_scene(image, labels) if labels else []
    return sorted((described(item, 'client') for item in detections),
                  key=lambda item: item['confidence'], reverse=True)


def detect_minimap_objects(panel, mode, kinds=None):
    """小地图面板里的小队圆环与敌人标记，坐标为面板像素。

    panel 由调用方按展开或紧凑模式裁好；圆环与普通敌人的精确中心仍应使用 perception 中
    对应的精定位，这里只给出检测框中心。
    """
    labels = kind_labels(requested(kinds, MINIMAP_KINDS), MINIMAP_LABELS)
    return sorted((described(item, 'minimap') for item in detect_minimap(panel, mode, labels)),
                  key=lambda item: item['confidence'], reverse=True)
