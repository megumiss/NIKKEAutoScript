"""普通与困难共用的章节底图资源。"""

from dev_tools.map_annotator import AnnotationStore
from dev_tools.map_paths import DEFAULT_MAPS_ROOT


def chapter_map(chapter):
    if not isinstance(chapter, int) or not 1 <= chapter <= 99:
        raise ValueError('Invalid chapter number.')
    store = AnnotationStore(DEFAULT_MAPS_ROOT)
    package = store.package(f'chapter_{chapter:02d}')
    metadata, digest, size, _ = store.metadata(package)
    if int(metadata['chapter']) != chapter:
        raise ValueError('Chapter differs from map metadata.')
    return package, {'chapter': chapter, 'size': size, 'image_sha256': digest}
