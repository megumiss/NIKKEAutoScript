from module.logger import logger

import requests
from tqdm import tqdm
from pathlib import Path

RootPath = Path(__file__).parent.parent


def download_with_progressbar(url: str, save_path: Path):
    """
    下载文件
    :param url:  下载链接
    :param save_path:  保存路径
    :return:
    """
    logger.debug(f"下载链接：{url}, 保存路径：{save_path}")
    try:
        response = requests.get(url, stream=True)
        if response.status_code != 200:
            raise Exception("下载失败！")
        total_size_in_bytes = int(response.headers.get("content-length", 1))
        block_size = 1024  # 1 Kibibyte
        progress_bar = tqdm(total=total_size_in_bytes, unit="iB", unit_scale=True)
        with open(save_path, "wb") as file:
            for data in response.iter_content(block_size):
                progress_bar.update(len(data))
                file.write(data)
        progress_bar.close()
    except:
        logger.error("下载文件失败！可尝试手动下载！")
        logger.error("下载链接：" + url)
        modelsPath = save_path.parent
        logger.error(f"解压后保存路径：{modelsPath}")
        if save_path.exists():
            save_path.unlink()
        raise Exception("下载文件失败！")
