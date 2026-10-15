"""普通与困难共用的章节底图资源。"""

from module.campaign.annotations import AnnotationStore
from module.campaign.paths import DEFAULT_MAPS_ROOT


def chapter_map(chapter):
    """按章节号取出运行地图包，并返回与之对应的底图摘要。

    地图来自资源仓库同步到 `data/resources/maps/` 的目录，普通与困难共用同一份底图，
    所以这里不接受难度参数。元数据里的章节号要与请求一致，否则说明资源目录被改过或同步错位，
    此时宁可报错也不要用错章的地图定位。返回的 image_sha256 用于上层判断底图是否换过。
    """
    if not isinstance(chapter, int) or not 1 <= chapter <= 99:
        raise ValueError('Invalid chapter number.')
    store = AnnotationStore(DEFAULT_MAPS_ROOT)
    package = store.package(f'chapter_{chapter:02d}')
    metadata, digest, size, _ = store.metadata(package)
    if int(metadata['chapter']) != chapter:
        raise ValueError('Chapter differs from map metadata.')
    return package, {'chapter': chapter, 'size': size, 'image_sha256': digest}
