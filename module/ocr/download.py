import tarfile
from pathlib import Path

from module.base.download import download_with_progressbar
from module.logger import logger


def maybe_download(model_storage_directory: Path, url: str) -> str:
    """
    下载模型
    :param model_storage_directory:  模型存储目录
    :param url:  模型下载地址
    :return:
    """
    if not model_storage_directory.exists():
        model_storage_directory.mkdir(parents=True)
    tar_file_name_list = ['.pdiparams', '.json', '.yml']
    if not (model_storage_directory / 'inference.pdiparams').exists():
        assert url.endswith('.tar'), 'Only supports tar compressed package'
        tmp_path = model_storage_directory / url.split('/')[-1]
        logger.info('PaddleOCR downloading {} to {}'.format(url, tmp_path))
        
        download_with_progressbar(url, tmp_path)
        with tarfile.open(tmp_path, 'r') as tarObj:
            for member in tarObj.getmembers():
                filename = None
                for tar_file_name in tar_file_name_list:
                    if member.name.endswith(tar_file_name):
                        filename = 'inference' + tar_file_name
                if filename is None:
                    continue
                file = tarObj.extractfile(member)
                with open(model_storage_directory / filename, 'wb') as f:
                    f.write(file.read())
        tmp_path.unlink()
    return str(model_storage_directory)
