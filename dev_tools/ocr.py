from paddleocr import PaddleOCR
import time
import os

def run_ocr(image_path, det_model=None, rec_model=None, lang='ch'):
    """
    执行OCR识别（无方向分类）
    
    参数:
    image_path: 图片路径
    det_model: 自定义检测模型路径（可选）
    rec_model: 自定义识别模型路径（可选）
    lang: 识别语言（默认'ch'中文）
    """
    # 初始化OCR模型配置
    ocr_config = {
        'use_angle_cls': False,  # 核心：禁用方向分类
        'lang': lang,            # 识别语言
        'use_gpu': False         # 是否使用GPU
    }
    
    # 添加自定义模型路径（如果提供）
    if det_model and os.path.exists(det_model):
        ocr_config['det_model_dir'] = det_model
        print(f"使用自定义检测模型: {det_model}")
    
    if rec_model and os.path.exists(rec_model):
        ocr_config['rec_model_dir'] = rec_model
        print(f"使用自定义识别模型: {rec_model}")
    
    # 初始化OCR引擎
    ocr = PaddleOCR(**ocr_config)
    
    # 执行OCR识别（确保不调用方向分类）
    start_time = time.time()
    result = ocr.ocr(image_path, cls=False)  # cls=False 确保不调用方向分类
    elapsed = time.time() - start_time
    
    # 提取所有识别文本
    all_text = []
    for page in result:
        for line in page:
            text, confidence = line[1]
            all_text.append(text)
            print(f"文本: {text} | 置信度: {confidence:.4f}")
    
    # 打印汇总信息
    print("\n===== OCR 结果汇总 =====")
    print(f"图片路径: {image_path}")
    print(f"识别耗时: {elapsed:.2f} 秒")
    print(f"检测到文本行数: {len(all_text)}")
    print(f"完整文本内容:\n{' '.join(all_text)}")
    
    return all_text

if __name__ == "__main__":
    # ===== 配置区域 =====
    TEST_IMAGE = "test1.png"  # 测试图片路径
    
    # 可选：自定义模型路径（设为None则使用默认模型）
    CUSTOM_DET_MODEL = 'bin\paddleocr\PP-OCRv5_mobile_det_pretrained.pdparams'  # 例如："./models/ch_PP-OCRv4_det_infer"
    CUSTOM_REC_MODEL = 'bin\paddleocr\PP-OCRv5_mobile_rec_pretrained.pdparams'  # 例如："./models/ch_PP-OCRv4_rec_infer"
    
    # 识别语言（'ch'中文，'en'英文）
    LANGUAGE = 'ch'
    # ===================
    
    # 运行OCR识别
    result_texts = run_ocr(
        image_path=TEST_IMAGE,
        det_model=CUSTOM_DET_MODEL,
        rec_model=CUSTOM_REC_MODEL,
        lang=LANGUAGE
    )
    