"""分层地图共用的俯视坐标；高程只参与截图投影，不改变标注图的二维坐标。"""

import numpy as np

from .surface_motion import project


def orthographic_chart(projection, focal, shape):
    """由相机内参恢复等距平面坐标，去掉投影拟合残留的两轴比例差。"""
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
    """将世界平面 h=a*x+b*y+c 投回截图；h 为相对相机高度，允许有坡度。"""
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
        """绑定所有表面到同一俯视画布；调用者必须明确表面身份，不能由二维交叉猜测。"""
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
        """未知高度不能回退为地面。"""
        if identifier not in self.surfaces:
            raise ValueError('Unknown surface identity')
        return self.surfaces[identifier]

    def roi_to_map(self, frame, identifier):
        """同一像素属于不同高度时返回不同映射，供定位和 Wiki 标注共用。"""
        if type(frame) is not int or not 0 <= frame < len(self.cameras):
            raise ValueError('Unknown camera frame')
        inverse = surface_to_roi(self.projection, self.vertical, self.cameras[frame], self._surface(identifier))
        return self.display @ np.linalg.inv(inverse)

    def map_to_roi(self, frame, identifier):
        """将已绑定表面的地图目标返回到相应视角，而不是使用全图单一单应。"""
        return np.linalg.inv(self.roi_to_map(frame, identifier))

    def map_point(self, frame, identifier, point):
        """将来源截图中的标注或小队位置写入原图像素坐标。"""
        result = project(self.roi_to_map(frame, identifier), point)
        self.height(identifier, result)
        return result

    def roi_point(self, frame, identifier, point):
        """检查表面位于相机下方后再计算目标的截图坐标。"""
        self.height(identifier, point)
        return project(self.map_to_roi(frame, identifier), point)

    def height(self, identifier, points):
        """按俯视坐标计算相对高度，不允许投影穿过相机高度。"""
        world = project(np.linalg.inv(self.display), points)
        coefficients = self._surface(identifier)
        height = world @ coefficients[:2] + coefficients[2]
        if not np.isfinite(height).all() or np.any(height >= .95):
            raise ValueError('Surface reaches or crosses the camera height')
        return height
