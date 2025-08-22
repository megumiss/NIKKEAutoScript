import re
from statistics import mean

import cv2
import numpy as np
from PIL import Image
from filelock import FileLock


def random_normal_distribution_int(a, b, n=3):
    """Generate a normal distribution int within the interval. Use the average value of several random numbers to
    simulate normal distribution.

    Args:
        a (int): The minimum of the interval.
        b (int): The maximum of the interval.
        n (int): The amount of numbers in simulation. Default to 3.

    Returns:
        int
    """
    if a < b:
        output = np.mean(np.random.randint(a, b, size=n))
        return int(output.round())
    else:
        return b

def random_rectangle_point(area, n=3):
    """Choose a random point in an area.

    Args:
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
        n (int): The amount of numbers in simulation. Default to 3.

    Returns:
        tuple(int): (x, y)
    """
    x = random_normal_distribution_int(area[0], area[2], n=n)
    y = random_normal_distribution_int(area[1], area[3], n=n)
    return x, y

def ensure_time(second, n=3, precision=3):
    """Ensure to be time.

    Args:
        second (int, float, tuple): time, such as 10, (10, 30), '10, 30'
        n (int): The amount of numbers in simulation. Default to 5.
        precision (int): Decimals.

    Returns:
        float:
    """
    if isinstance(second, tuple):
        multiply = 10 ** precision
        result = random_normal_distribution_int(second[0] * multiply, second[1] * multiply, n) / multiply
        return round(result, precision)
    elif isinstance(second, str):
        if ',' in second:
            lower, upper = second.replace(' ', '').split(',')
            lower, upper = int(lower), int(upper)
            return ensure_time((lower, upper), n=n, precision=precision)
        if '-' in second:
            lower, upper = second.replace(' ', '').split('-')
            lower, upper = int(lower), int(upper)
            return ensure_time((lower, upper), n=n, precision=precision)
        else:
            return int(second)
    else:
        return second


def image_size(image):
    """
    Args:
        image (np.ndarray):

    Returns:
        int, int: width, height
    """
    shape = image.shape
    return shape[1], shape[0]


def random_rectangle_point(area, n=3):
    """Choose a random point in an area.

    Args:
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
        n (int): The amount of numbers in simulation. Default to 3.

    Returns:
        tuple(int): (x, y)
    """
    x = random_normal_distribution_int(area[0], area[2], n=n)
    y = random_normal_distribution_int(area[1], area[3], n=n)
    return x, y


def rectangle_point(area):
    x = (area[0] + area[2]) / 2
    y = (area[1] + area[3]) / 2
    return x, y


def ensure_int(*args):
    """
    Convert all elements to int.
    Return the same structure as nested objects.

    Args:
        *args:

    Returns:
        list:
    """

    def to_int(item):
        try:
            return int(item)
        except TypeError:
            result = [to_int(i) for i in item]
            if len(result) == 1:
                result = result[0]
            return result

    return to_int(args)


def point2str(x, y, length=4):
    """
    Args:
        x (int, float):
        y (int, float):
        length (int): Align length.

    Returns:
        str: String with numbers right aligned, such as '( 100,  80)'.
    """
    return '(%s, %s)' % (str(int(x)).rjust(length), str(int(y)).rjust(length))


def load_image(file, area=None):
    """
    Load an image like pillow and drop alpha channel.

    Args:
        file (str):
        area (tuple):

    Returns:
        np.ndarray:
    """
    # import PIL
    # print("PIL module path:", PIL.__file__)
    # print("PIL.Image path:", PIL.Image.__file__)
    # print("PIL.Image attributes:", [attr for attr in dir(PIL.Image) if not attr.startswith('_')])
    image = Image.open(file)
    if area is not None:
        image = image.crop(area)
    image = np.array(image)
    channel = image.shape[2] if len(image.shape) > 2 else 1
    if channel > 3:
        image = image[:, :, :3].copy()
    return image


def image_channel(image):
    """
    Args:
        image (np.ndarray):

    Returns:
        int: 0 for grayscale, 3 for RGB.
    """
    return image.shape[2] if len(image.shape) == 3 else 0


def get_bbox(image, threshold=0):
    """
    A numpy implementation of the getbbox() in pillow.

    Args:
        image (np.ndarray): Screenshot.
        threshold (int): Color <= threshold will be considered black

    Returns:
        tuple: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
    """
    if image_channel(image) == 3:
        image = np.max(image, axis=2)
    x = np.where(np.max(image, axis=0) > threshold)[0]
    y = np.where(np.max(image, axis=1) > threshold)[0]
    return x[0], y[0], x[-1] + 1, y[-1] + 1


def crop(image, area):
    """
    Crop image like pillow, when using opencv / numpy.
    Provides a black background if cropping outside of image.

    Args:
        image (np.ndarray):
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)

    Returns:
        np.ndarray:
    """
    x1, y1, x2, y2 = map(int, map(round, area))
    h, w = image.shape[:2]
    border = np.maximum((0 - y1, y2 - h, 0 - x1, x2 - w), 0)
    x1, y1, x2, y2 = np.maximum((x1, y1, x2, y2), 0)
    image = image[y1:y2, x1:x2].copy()
    if sum(border) > 0:
        image = cv2.copyMakeBorder(image, *border, borderType=cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return image


def get_color(image, area):
    """Calculate the average color of a particular area of the image.

    Args:
        image (np.ndarray): Screenshot.
        area (tuple): (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)

    Returns:
        tuple: (r, g, b)
    """
    temp = crop(image, area)
    color = cv2.mean(temp)
    return color[:3]


def area_offset(area, offset):
    """

    Args:
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
        offset: (x, y).

    Returns:
        tuple: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
    """
    return tuple(np.array(area) + np.append(offset, offset))


def _area_offset(area, offset):
    """

    Args:
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
        offset: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).

    Returns:
        tuple: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
    """

    return tuple(np.array([i + x for i, x in zip(area, offset)]))


def color_similar(color1, color2, threshold=10):
    """Consider two colors are similar, if tolerance lesser or equal threshold.
    Tolerance = Max(Positive(difference_rgb)) + Max(- Negative(difference_rgb))
    The same as the tolerance in Photoshop.

    Args:
        color1 (tuple): (r, g, b)
        color2 (tuple): (r, g, b)
        threshold (int): Default to 10.

    Returns:
        bool: True if two colors are similar.
    """
    # print(color1, color2)
    diff = np.array(color1).astype(int) - np.array(color2).astype(int)
    diff = np.max(np.maximum(diff, 0)) - np.min(np.minimum(diff, 0))
    # print(diff)
    return diff <= threshold


def save_image(image, file):
    """
    Save an image like pillow.

    Args:
        image (np.ndarray):
        file (str):
    """
    # image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    # cv2.imwrite(file, image)
    Image.fromarray(image).save(file)


def extract_letters(image, letter=(255, 255, 255), threshold=128):
    """Set letter color to black, set background color to white.

    Args:
        image: Shape (height, width, channel)
        letter (tuple): Letter RGB.
        threshold (int):

    Returns:
        np.ndarray: Shape (height, width)
    """
    r, g, b = cv2.split(cv2.subtract(image, (*letter, 0)))
    positive = cv2.max(cv2.max(r, g), b)
    r, g, b = cv2.split(cv2.subtract((*letter, 0), image))
    negative = cv2.max(cv2.max(r, g), b)
    return cv2.multiply(cv2.add(positive, negative), 255.0 / threshold)


def float2str(n, decimal=3):
    """
    Args:
        n (float):
        decimal (int):

    Returns:
        str:
    """
    return str(round(n, decimal)).ljust(decimal + 2, "0")


def mask_area(image, area):
    """

    Args:
        image: np.ndarray
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).

    Returns:
        image: np.ndarray
    """

    mask = np.zeros(image.shape[:2], np.uint8)
    mask[area[1]:area[3], area[0]:area[2]] = 255
    image = cv2.bitwise_and(image, image, mask=cv2.bitwise_not(mask))
    return image


def find_letter_area(condition):
    pixels = np.where(condition)
    min_row, min_col = np.min(pixels, axis=1)
    max_row, max_col = np.max(pixels, axis=1)
    return min_col, min_row, max_col, max_row


def find_center(rect):
    """
    Args:
        rect: tuple = (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
    Returns:
        x, y: tuple
    """
    ul_x, ul_y, br_x, br_y = rect
    x = mean([ul_x, br_x])
    y = mean([ul_y, br_y])
    return x, y


def exec_file(file) -> dict:
    lock = FileLock(f"{file}.lock")
    with lock:
        result = {}
        with open(file, 'r', encoding='utf-8') as f:
            code = f.read()
            exec(code, result)
        del result['__builtins__']
        return result


def show_image(image, title='image', delay=0):
    """
    Args:
        image: np.ndarray
        title: str
        delay: int
    """

    cv2.imshow(title, image)
    cv2.waitKey(delay)

def area_pad(area, pad=10):
    """
    Inner offset an area.

    Args:
        area: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
        pad (int):

    Returns:
        tuple: (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y).
    """
    upper_left_x, upper_left_y, bottom_right_x, bottom_right_y = area
    return upper_left_x + pad, upper_left_y + pad, bottom_right_x - pad, bottom_right_y - pad

def remove_punctuation(text: str) -> str:
    """移除所有标点符号和空格"""
    pattern = r'[^\w]'
    return re.sub(pattern, '', text)

def get_button_by_location(buttons, coord='y', order='descending'):
    """
    根据指定的坐标参数和排序顺序返回对应的button
    
    参数:
    coord -- 排序坐标: 'x' 或 'y' (默认为 'y')
    order -- 排序顺序: 'ascending' (升序) 或 'descending' (降序) (默认为 'descending')
    
    返回:
    根据排序条件选定的button，如果列表为空则返回 None
    """
    if not buttons:
        return None
    
    # 确定坐标索引
    coord_index = 0 if coord == 'x' else 1
    
    # 确定比较函数
    if order == 'ascending':
        compare = lambda a, b: a < b
    else:  # descending
        compare = lambda a, b: a > b
    
    # 初始化最值对象
    result_bn = buttons[0]
    result_value = result_bn.location[coord_index]
    
    # 遍历所有对象
    for bn in buttons[1:]:
        current_value = bn.location[coord_index]
        
        # 根据比较函数更新结果
        if compare(current_value, result_value):
            result_value = current_value
            result_bn = bn
    
    return result_bn

def rgb2luma(image):
    """
    Convert RGB to the Y channel (Luminance) in YUV color space.

    Args:
        image (np.ndarray): Shape (height, width, channel)

    Returns:
        np.ndarray: Shape (height, width)
    """
    image = cv2.cvtColor(image, cv2.COLOR_RGB2YUV)
    luma, _, _ = cv2.split(image)
    return luma

def sort_buttons_by_location(buttons):
    """
    返回排序后的button列表
    
    排序规则:
    - 首先按y坐标升序排序 (y越小越靠前)
    - 如果两个按钮的y值相差在30像素内，则视为同一行
    - 同一行内的按钮按x坐标升序排序 (x越小越靠前)
    
    返回:
    排序后的按钮列表，如果列表为空则返回空列表
    """
    if not buttons:
        return []
    
    # 第一步：按y坐标升序排序
    sorted_buttons = sorted(buttons, key=lambda bn: bn.location[1])
    
    # 第二步：使用连通分量算法分组（基于y值相差≤30像素的条件）
    groups = []  # 存储分组结果的列表
    for bn in sorted_buttons:
        found_groups = []  # 存储需要合并的分组索引
        
        # 检查当前按钮与所有现有分组的连通性
        for idx, group in enumerate(groups):
            for other_bn in group:
                if abs(bn.location[1] - other_bn.location[1]) <= 30:
                    found_groups.append(idx)
                    break  # 找到一个连通分组即停止检查当前分组
        
        # 处理连通分组
        if not found_groups:
            # 无连通分组时创建新组
            groups.append([bn])
        else:
            # 创建新合并组（包含当前按钮和所有连通分组）
            new_group = [bn]
            # 从后向前合并分组（避免索引变化问题）
            for idx in sorted(found_groups, reverse=True):
                new_group.extend(groups[idx])
                del groups[idx]
            groups.append(new_group)
    
    # 第三步：对每个分组内部排序（按x升序，x相同则按y升序）
    for group in groups:
        group.sort(key=lambda bn: (bn.location[0], bn.location[1]))
    
    # 第四步：对分组排序（按组内最小y值升序）
    groups.sort(key=lambda group: min(bn.location[1] for bn in group))
    
    # 第五步：展平分组列表
    result = []
    for group in groups:
        result.extend(group)
    
    return result

def random_line_segments(p1, p2, n, random_range=(0, 0, 0, 0)):
    """Cut a line into multiple segments.

    Args:
        p1: (x, y).
        p2: (x, y).
        n: Number of slice.
        random_range: Add a random_range to points.

    Returns:
        list[tuple]: [(x0, y0), (x1, y1), (x2, y2)]
    """
    return [tuple((((n - index) * p1 + index * p2) / n).astype(int) + random_rectangle_point(random_range))
            for index in range(0, n + 1)]
