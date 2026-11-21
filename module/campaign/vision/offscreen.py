"""小队离屏标记：白色圆环加一个朝外的小三角，出现即说明小队不在画面内，点圆心可把小队移回中央。

不走 YOLO。环半径与三角形状固定，环内头像会变，所以只验证几何，不比对内容。
亮背景会淹没三角，这种帧宁可漏报：标记会一直显示到小队回到画面，下一帧还有机会；
而圆形场景物一旦被误认，后续逻辑就会去点它。
"""

import cv2
import numpy as np

CLIENT_SIZE = (1776, 999)
# 1776×999 客户区上环半径实测 32～33 像素；环带与环外一圈按此设定。
RADIUS = 32.5
RING_BAND = 3
OUTER_BAND = (5, 11)
SECTORS = 36
RING_COVERAGE = .95
# 环带白占比：细环实测 0.34～0.40，粗白边的圆形物 0.47 以上，实心白盘为 1；候选排序也按接近典型值。
RING_FILL = (.28, .45)
FILL_TYPICAL = .37
# 环外一圈只允许尾巴这一小段连续高亮。
OUTER_COVERAGE = .45
TAIL_RUN = (2, 8)
# 尾巴必须朝画面外侧：在圆心所在象限上，任一轴不得明显指回画面中心，且至少一轴明显指向外侧。
OUTWARD_BACK = -.5
OUTWARD_MIN = .7
# 候选圆心在半分辨率上找：环带响应减去环外一圈响应，细环约 0.2，实心或纹理亮区接近 0；
# 只按响应排序会让淡色地面上成百上千的亮斑把细环挤出名单。
SCALE = 2
RESPONSE = (.15, .75)
INNER_MAX = .9
CONTRAST = .10
PEAKS = 64

REACH = int(np.ceil(RADIUS + OUTER_BAND[1])) + 1
_yy, _xx = np.mgrid[-REACH:REACH + 1, -REACH:REACH + 1]
_distance = np.hypot(_xx, _yy)
SECTOR = (np.degrees(np.arctan2(_yy, _xx)) % 360 * SECTORS / 360).astype(int) % SECTORS
RING = (_distance >= RADIUS - RING_BAND) & (_distance <= RADIUS + RING_BAND)
OUTER = (_distance >= RADIUS + OUTER_BAND[0]) & (_distance <= RADIUS + OUTER_BAND[1])


def annulus(radius, band):
    """归一化的圆环卷积核，用于在半分辨率上一次算出环带亮度占比。

    核做了归一化，卷积结果直接就是环带内亮像素的比例，RESPONSE、CONTRAST 这些阈值才能按
    比例写成固定值；传入的半径和环宽都应先除以 SCALE。
    """
    reach = int(np.ceil(radius + band))
    yy, xx = np.mgrid[-reach:reach + 1, -reach:reach + 1]
    distance = np.hypot(xx, yy)
    kernel = ((distance >= radius - band) & (distance <= radius + band)).astype(np.float32)
    return kernel / kernel.sum()


def disc(radius):
    """归一化的实心圆卷积核，用来量环内亮度。

    与 annulus 同样做归一化，响应即圆内亮像素比例；环内是会变的头像，占比接近 1 说明是
    实心白盘之类的场景物，可以据此（INNER_MAX）排掉。
    """
    reach = int(np.ceil(radius))
    yy, xx = np.mgrid[-reach:reach + 1, -reach:reach + 1]
    kernel = (np.hypot(xx, yy) <= radius).astype(np.float32)
    return kernel / kernel.sum()


KERNEL = annulus(RADIUS / SCALE, RING_BAND / SCALE)
INNER = disc((RADIUS - RING_BAND - 2) / SCALE)
OUTER_KERNEL = annulus((RADIUS + sum(OUTER_BAND) / 2) / SCALE, (OUTER_BAND[1] - OUTER_BAND[0]) / 2 / SCALE)


def bright(image):
    """接近白色的掩码；环与三角都是高亮度低饱和，头像与场景一般不是。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return (hsv[:, :, 2] > 200) & (hsv[:, :, 1] < 60)


def longest_run(flags):
    """环形扇区里最长的一段连续命中，返回长度与起始扇区。"""
    length = len(flags)
    best, best_start, current = 0, None, 0
    for index, value in enumerate(np.concatenate([flags, flags])):
        if not value:
            current = 0
            continue
        current += 1
        if current > best:
            best, best_start = current, index - current + 1
    if best_start is None:
        return 0, None
    return min(best, length), best_start % length


def measure(mask, cx, cy):
    """以整数圆心统计环带与环外一圈的亮像素；贴近画面边缘无法取样时返回 None。"""
    cx, cy = int(round(cx)), int(round(cy))
    if cx - REACH < 0 or cy - REACH < 0 or cx + REACH >= mask.shape[1] or cy + REACH >= mask.shape[0]:
        return None
    patch = mask[cy - REACH:cy + REACH + 1, cx - REACH:cx + REACH + 1]
    ring_hit = np.zeros(SECTORS, bool)
    ring_hit[SECTOR[RING & patch]] = True
    outer_hit = np.zeros(SECTORS, bool)
    outer_hit[SECTOR[OUTER & patch]] = True
    run, start = longest_run(outer_hit)
    return dict(fill=float(patch[RING].mean()), coverage=float(ring_hit.mean()),
                outer_coverage=float(outer_hit.mean()), tail_run=run, tail_start=start)


def tail_verified(score):
    """环外一圈的高亮是否符合「只有一条朝外小三角」的形态。

    真标记在环外只多出尾巴这一小段连续扇区，所以整圈亮度占比要低，且最长连续段落在
    TAIL_RUN 之内：太短是噪声毛刺，太长则是粗白边或亮背景，都不能当成尾巴。
    """
    return score['outer_coverage'] <= OUTER_COVERAGE and TAIL_RUN[0] <= score['tail_run'] <= TAIL_RUN[1]


def tail_direction(score):
    """三角指向，度，0 为正右、90 为正下。"""
    return (score['tail_start'] + score['tail_run'] / 2) * 360 / SECTORS % 360


def outward(score, cx, cy):
    """尾巴是否朝画面外侧。

    顶边中部以外的标记尾巴朝正上而不是朝画面中心的反方向，所以按象限逐轴判断：
    任一轴不能明显指回中心，且至少一轴明显指向外侧。
    """
    radians = np.deg2rad(tail_direction(score))
    tx, ty = np.cos(radians), np.sin(radians)
    sx, sy = np.sign(cx - CLIENT_SIZE[0] / 2), np.sign(cy - CLIENT_SIZE[1] / 2)
    along = (tx * sx, ty * sy)
    return min(along) >= OUTWARD_BACK and max(along) >= OUTWARD_MIN


def accepted(score, cx, cy):
    """完整的细白环，带一段朝外的连续高亮尾巴，其余环外一圈都暗。"""
    if score is None or score['coverage'] < RING_COVERAGE or not RING_FILL[0] <= score['fill'] <= RING_FILL[1]:
        return False
    return tail_verified(score) and outward(score, cx, cy)


def response_map(mask):
    """半分辨率上的环带、环内圆盘与环外一圈的模板响应。"""
    small = cv2.resize(mask.astype(np.float32), None, fx=1 / SCALE, fy=1 / SCALE, interpolation=cv2.INTER_AREA)
    return tuple(cv2.filter2D(small, -1, kernel, borderType=cv2.BORDER_CONSTANT)
                 for kernel in (KERNEL, INNER, OUTER_KERNEL))


def peaks(mask):
    """细环区间内、与环外有对比的响应局部极大值，按对比从高到低给出全分辨率圆心。"""
    response, inner, outer = response_map(mask)
    local = cv2.dilate(response, np.ones((5, 5), np.uint8))
    contrast = response - outer
    selected = ((response >= RESPONSE[0]) & (response <= RESPONSE[1]) & (inner <= INNER_MAX)
                & (response >= local) & (contrast >= CONTRAST))
    ys, xs = np.nonzero(selected)
    order = np.argsort(-contrast[ys, xs])[:PEAKS]
    return [(int(xs[i]) * SCALE + SCALE // 2, int(ys[i]) * SCALE + SCALE // 2) for i in order]


def refine(mask, cx, cy):
    """在候选圆心周围按 4、2、1 像素逐级搜索环覆盖最高、环带占比最接近典型值的位置。

    不把“能否通过检验”放进排序：让精修去找一个勉强能过的落点，会把残缺的圆形场景物也放进来。
    """
    best = None
    for step in (4, 2, 1):
        base = (cx, cy) if best is None else (best[1], best[2])
        for dy in (-step, 0, step):
            for dx in (-step, 0, step):
                x, y = base[0] + dx, base[1] + dy
                score = measure(mask, x, y)
                if score is None:
                    continue
                rank = (score['coverage'], -abs(score['fill'] - FILL_TYPICAL))
                if best is None or rank > best[0]:
                    best = (rank, x, y, score)
    return best


def squad_offscreen(image):
    """返回小队离屏标记，没有则 None。

    image 为 1776×999 的 BGR 客户区截图。返回 center（点击点）、radius、direction（三角指向，
    度，0 为正右、90 为正下）与 coverage、fill 证据；多个候选取环覆盖最高、占比最接近典型值的。
    """
    if image is None or image.ndim != 3 or image.shape[1::-1] != CLIENT_SIZE:
        raise ValueError('小队离屏标记识别需要 1776×999 的 BGR 客户区截图。')
    mask = bright(image)
    best = None
    for cx, cy in peaks(mask):
        refined = refine(mask, cx, cy)
        if refined is None:
            continue
        rank, x, y, score = refined
        if not accepted(score, x, y) or (best is not None and rank <= best[0]):
            continue
        best = (rank, x, y, score)
    if best is None:
        return None
    _, cx, cy, score = best
    return dict(center=[float(cx), float(cy)], radius=RADIUS, direction=round(tail_direction(score), 1),
                coverage=round(score['coverage'], 3), fill=round(score['fill'], 3))
