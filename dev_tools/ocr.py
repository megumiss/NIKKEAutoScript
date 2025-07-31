from paddleocr import PaddleOCR
import os
import time

def init_ocr(det_model=None, rec_model=None, lang='ch'):
    """
    初始化并返回OCR实例（只需执行一次）
    
    参数:
    det_model: 自定义检测模型路径（可选）
    rec_model: 自定义识别模型路径（可选）
    lang: 识别语言（默认'ch'中文）
    """
    print("正在初始化OCR引擎，首次加载可能需要较长时间...")
    start_time = time.time()
    
    ocr = PaddleOCR(
        lang=lang,
        ocr_version="PP-OCRv5",
        text_detection_model_name='PP-OCRv5_mobile_det',
        text_detection_model_dir=det_model,
        text_recognition_model_name='PP-OCRv5_mobile_rec',
        text_recognition_model_dir=rec_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_thresh=0.1,
        text_det_unclip_ratio=5
    )
    
    # 预热（可选）：使用一张小图片让模型完成初始化
    try:
        ocr.predict("warmup.png")  # 可以创建一个1x1像素的图片文件
    except:
        pass
    
    print(f"OCR引擎初始化完成，耗时: {time.time()-start_time:.2f}秒")
    return ocr

def run_ocr(ocr_instance, image_path):
    """
    使用已初始化的OCR实例执行识别
    
    参数:
    ocr_instance: 已初始化的PaddleOCR实例
    image_path: 图片路径
    """
    return ocr_instance.predict(image_path)

if __name__ == "__main__":
    # ===== 配置区域 =====
    INPUT_DIR = "E:\\AutoGame\\NIKKECnOCR\\train\\number\\images"
    OUTPUT_FILE = "ocr_results.txt"  # 结果输出文件
    
    # 支持识别的图片格式
    IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.bmp', '.tiff']
    
    # 可选：自定义模型路径
    CUSTOM_DET_MODEL = 'bin\\paddleocr\\PP-OCRv5_mobile_det_infer'
    CUSTOM_REC_MODEL = 'bin\\paddleocr\\PP-OCRv5_mobile_rec_infer'
    
    # 识别语言（'ch'中文，'en'英文）
    LANGUAGE = 'en'
    # ===================
    
    # 获取所有图片文件
    image_files = []
    for filename in os.listdir(INPUT_DIR):
        if any(filename.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
            image_files.append(os.path.join(INPUT_DIR, filename))
    
    if not image_files:
        print(f"在 {INPUT_DIR} 目录中未找到图片文件")
        exit()
    
    # 初始化OCR实例（只需一次）
    ocr_engine = init_ocr(
        det_model=CUSTOM_DET_MODEL,
        rec_model=CUSTOM_REC_MODEL,
        lang=LANGUAGE
    )
    
    print(f"\n找到 {len(image_files)} 张图片，开始OCR识别...")
    start_time = time.time()
    
    # 打开结果文件准备写入
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f_out:
        # 处理每张图片（使用同一个OCR实例）
        for i, img_path in enumerate(image_files):
            print(f"\n处理图片 ({i+1}/{len(image_files)}): {os.path.basename(img_path)}")
            try:
                # 执行OCR（复用实例）
                results = run_ocr(ocr_engine, img_path)
                
                # 提取识别文本
                all_text = []
                for result in results:
                    all_text.extend(result['rec_texts'])
                
                # 拼接文本结果
                combined_text = ''.join(all_text)
                
                # 输出结果到控制台和文件
                print(f"识别结果: {combined_text}")
                f_out.write(f"图片: {os.path.basename(img_path)}\n")
                f_out.write(f"文本: {combined_text}\n")
                f_out.write("-" * 50 + "\n")
                
            except Exception as e:
                error_msg = f"处理 {img_path} 时出错: {str(e)}"
                print(error_msg)
                f_out.write(error_msg + "\n")
    
    # 性能统计
    elapsed = time.time() - start_time
    avg_time = elapsed / len(image_files) if image_files else 0
    print(f"\n处理完成! 共处理 {len(image_files)} 张图片")
    print(f"总耗时: {elapsed:.2f}秒 | 平均每张: {avg_time:.2f}秒")
    print(f"识别结果已保存到: {os.path.abspath(OUTPUT_FILE)}")

