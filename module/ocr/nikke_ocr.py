from pathlib import Path
from typing import Union, List, Dict, Any, Tuple

import numpy as np
import torch
from PIL import Image
from cnocr import CnOcr

from module.base.utils import crop


class NikkeOcr(CnOcr):
    def __init__(self, rec_model_name='densenet_lite_136-gru', det_model_name='ch_PP-OCRv4_det', cand_alphabet=None,
                 context='cpu',
                 root='./bin/cnocr_models/nikke',
                 model_name='/t25.ckpt', **kwargs):
        model_fp = root + model_name
        super().__init__(rec_model_name=rec_model_name, det_model_name=det_model_name, rec_model_fp=model_fp,
                         cand_alphabet=cand_alphabet, context=context,
                         rec_model_backend='pytorch',
                         **kwargs)

    def ocr(
            self,
            img_fp: Union[str, Path, Image.Image, torch.Tensor, np.ndarray],
            rec_batch_size=1,
            return_cropped_image=False,
            area: Tuple = None,
            **det_kwargs,
    ) -> List[Dict[str, Any]]:
        if area and isinstance(img_fp, (Image.Image, np.ndarray)):
            img_fp = crop(img_fp, area)

        return super(NikkeOcr, self).ocr(img_fp, rec_batch_size, return_cropped_image, **det_kwargs)
