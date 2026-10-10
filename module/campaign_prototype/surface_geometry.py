"""分层地图共用的俯视坐标；高程只参与截图投影，不改变标注图的二维坐标。"""

import numpy as np

from .surface_motion import project


def orthographic_chart(projection, focal, shape):
    """由相机内参恢复等距平面坐标，去掉投影拟合残留的两轴比例差。

    projection 为截图到参考平面的矩阵，focal 为正焦距，shape 按高、宽给出。
    校验恢复的地面轴正交及无残余透视后返回等面积尺度校正，不能用退化相机参数生成俯视坐标。
    """
    projection = np.asarray(projection, float)
    if (projection.shape != (3, 3) or not np.isfinite(projection).all()
            or abs(np.linalg.det(projection)) < 1e-10 or not np.isfinite(focal) or focal <= 0):
        raise ValueError('Invalid metric projection')
    shape = np.asarray(shape, float)
    if shape.shape != (2,) or not np.isfinite(shape).all() or np.any(shape <= 0):
        raise ValueError('Invalid source image dimensions')
    height, width = shape
    intrinsic = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1.]])
    inverse = np.linalg.inv(intrinsic)
    axes = inverse @ np.linalg.inv(projection)[:, :2]
    axes /= np.linalg.norm(axes, axis=0)
    if abs(axes[:, 0] @ axes[:, 1]) > .01:
        raise ValueError('Camera axes do not establish an orthogonal ground plane')
    metric = np.vstack([axes.T, np.cross(axes[:, 0], axes[:, 1])]) @ inverse
    correction = metric @ np.linalg.inv(projection)
    if abs(correction[2, 2]) < 1e-10:
        raise ValueError('Metric plane lies on the horizon')
    correction /= correction[2, 2]
    if not np.allclose(correction[2], [0, 0, 1], atol=1e-8):
        raise ValueError('Projection does not rectify the fitted ground plane')
    correction[:2] /= np.sqrt(abs(np.linalg.det(correction[:2, :2])))
    correction[:2, 2] = 0
    return correction


def surface_to_roi(projection, vertical, camera, coefficients):
    """将世界平面 h=a*x+b*y+c 投回截图；h 为相对相机高度，允许有坡度。

    coefficients=(a,b,c) 表示世界平面 h=a*x+b*y+c，camera 和 vertical 使用对应世界坐标。
    返回世界二维平面→ROI 的单应，坡度参与齐次分母；参数形状错误或变换退化时拒绝。
    """
    projection = np.asarray(projection, float)
    vertical, camera, coefficients = [np.asarray(value, float) for value in (vertical, camera, coefficients)]
    if (projection.shape != (3, 3) or vertical.shape != (2,) or camera.shape != (2,)
            or coefficients.shape != (3,)
            or not all(np.isfinite(value).all() for value in (projection, vertical, camera, coefficients))
            or abs(np.linalg.det(projection)) < 1e-10):
        raise ValueError('Invalid surface camera geometry')
    a, b, c = coefficients
    vx, vy = vertical
    chart = np.array([[1 - vx * a, -vx * b, -camera[0] - vx * c],
                      [-vy * a, 1 - vy * b, -camera[1] - vy * c], [-a, -b, 1 - c]])
    matrix = np.linalg.solve(projection, chart)
    if abs(np.linalg.det(matrix)) < 1e-10:
        raise ValueError('Surface projection is degenerate')
    return matrix


class SurfaceGeometry:
    def __init__(self, projection, vertical, cameras, surfaces, world_to_map):
        """绑定所有表面到同一俯视画布；调用者必须明确表面身份，不能由二维交叉猜测。

        接收相机位置列表、按字符串身份索引的高度平面和仿射 world_to_map 显示矩阵。
        逐项验证并存储几何，不推断表面间可通行性；空表面、非法相机或非仿射画布会在初始化时失败。
        """
        self.projection = np.asarray(projection, float)
        self.vertical = np.asarray(vertical, float)
        self.cameras = np.asarray(cameras, float)
        self.display = np.asarray(world_to_map, float)
        if (self.cameras.ndim != 2 or self.cameras.shape[1] != 2 or len(self.cameras) == 0
                or not np.isfinite(self.cameras).all() or self.display.shape != (3, 3)
                or not np.isfinite(self.display).all() or abs(np.linalg.det(self.display)) < 1e-10
                or not np.allclose(self.display[2], [0, 0, 1], atol=1e-10)):
            raise ValueError('Expected an affine orthographic display and finite cameras')
        if not isinstance(surfaces, dict) or not surfaces:
            raise ValueError('Missing surface identities')
        self.surfaces = {}
        for identifier, coefficients in surfaces.items():
            if not isinstance(identifier, str) or not identifier:
                raise ValueError('Surface identity must be a nonempty string')
            surface_to_roi(self.projection, self.vertical, self.cameras[0], coefficients)
            self.surfaces[identifier] = np.asarray(coefficients, float)

    def _surface(self, identifier):
        """未知高度不能回退为地面。

        按 identifier 查找已绑定高度平面的三个系数。
        不存在即抛出 ValueError，避免把未知层默认为零高度而产生看似合理的错误落点。
        """
        if identifier not in self.surfaces:
            raise ValueError('Unknown surface identity')
        return self.surfaces[identifier]

    def roi_to_map(self, frame, identifier):
        """同一像素属于不同高度时返回不同映射，供定位和 Wiki 标注共用。

        frame 是有效相机索引，identifier 必须指明该像素所在的道路表面。
        反解对应表面的截图投影后组合共享画布，返回 3×3 ROI→map 单应。
        """
        if type(frame) is not int or not 0 <= frame < len(self.cameras):
            raise ValueError('Unknown camera frame')
        inverse = surface_to_roi(self.projection, self.vertical, self.cameras[frame], self._surface(identifier))
        return self.display @ np.linalg.inv(inverse)

    def map_to_roi(self, frame, identifier):
        """将已绑定表面的地图目标返回到相应视角，而不是使用全图单一单应。

        使用指定帧和表面的 ROI→map 矩阵求逆。
        返回 map→ROI 单应，帧与身份验证由 roi_to_map 完成；不能跨表面复用结果。
        """
        return np.linalg.inv(self.roi_to_map(frame, identifier))

    def map_point(self, frame, identifier, point):
        """将来源截图中的标注或小队位置写入原图像素坐标。

        把指定帧、指定表面的 ROI 点投影到共享地图像素。
        投影后检查对应高度未接近相机平面；不满足物理高度限制时抛错。
        """
        result = project(self.roi_to_map(frame, identifier), point)
        self.height(identifier, result)
        return result

    def roi_point(self, frame, identifier, point):
        """检查表面位于相机下方后再计算目标的截图坐标。

        point 是共享地图坐标，先恢复其世界高度再反投影到 frame 的 ROI。
        未知表面、接近相机高度或投影退化均失败，返回值不自动检查截图可见范围。
        """
        self.height(identifier, point)
        return project(self.map_to_roi(frame, identifier), point)

    def height(self, identifier, points):
        """按俯视坐标计算相对高度，不允许投影穿过相机高度。

        将地图点反解到世界坐标后计算所属平面的相对相机高度，支持单点和点集。
        任一点高度非有限或达到 0.95 及以上都会拒绝，以避免接近相机平面的奇异投影。
        """
        world = project(np.linalg.inv(self.display), points)
        coefficients = self._surface(identifier)
        height = world @ coefficients[:2] + coefficients[2]
        if not np.isfinite(height).all() or np.any(height >= .95):
            raise ValueError('Surface reaches or crosses the camera height')
        return height
