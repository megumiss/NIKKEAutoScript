"""移动和实验命令共用的证据写盘检查，不持有窗口或实验全局状态。"""

import cv2


def write_image(path, image, *args):
    """保存证据图片并检查 OpenCV 返回值，写盘失败必须上报而不能继续假装已留证。

    path 为输出路径，image 为 OpenCV 图像，附加参数直接传给 imwrite。
    写入返回失败时抛出 OSError，避免生成成功回执却缺失现场证据。
    """
    if image is None or not cv2.imwrite(str(path), image, *args):
        raise OSError(f'Could not save evidence image: {path}')
    return True
