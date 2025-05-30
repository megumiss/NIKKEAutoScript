import json
import re
from functools import cached_property
from pathlib import Path

class Dialogue:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self._raw_data = None
    
    @property
    def raw_data(self):
        """加载原始JSON数据"""
        if self._raw_data is None:
            if not self.file_path.exists():
                raise FileNotFoundError(f"文件不存在: {self.file_path}")
            
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self._raw_data = json.load(f)
            except json.JSONDecodeError:
                raise ValueError(f"无效的JSON文件: {self.file_path}")
            except Exception as e:
                raise RuntimeError(f"加载文件时出错: {str(e)}")
        
        return self._raw_data
    
    @cached_property
    def dialogue_data(self):
        """处理后的数据"""
        def remove_punctuation(text):
            """移除所有标点符号和空格"""
            pattern = r'[^\w]'
            return re.sub(pattern, '', text)
        
        def process_data(obj):
            """递归处理数据结构中的所有字符串"""
            if isinstance(obj, dict):
                new_dict = {}
                for key, value in obj.items():
                    # 处理键（如果是字符串）
                    cleaned_key = remove_punctuation(key) if isinstance(key, str) else key
                    # 递归处理值
                    new_dict[cleaned_key] = process_data(value)
                return new_dict
            elif isinstance(obj, list):
                return [process_data(item) for item in obj]
            elif isinstance(obj, str):
                return remove_punctuation(obj)
            else:
                return obj
        
        return process_data(self.raw_data)

    def get_correct_answer(self, character, answer1, answer2):
        """
        返回两个答案中正确的那个
        """
        # 清理文本的辅助函数
        def clean_text(text):
            pattern = r'[^\w]'
            return re.sub(pattern, '', text)
        
        # 清理角色名称
        cleaned_character = clean_text(character)
        
        # 获取角色数据
        character_data = self.dialogue_data.get(cleaned_character)
        if not character_data:
            raise ValueError(f"角色 '{character}' 不存在（清理后: '{cleaned_character}'）")
        
        # 清理两个答案
        cleaned_answer1 = clean_text(answer1)
        cleaned_answer2 = clean_text(answer2)
        
        # 在角色的所有问题中查找匹配的答案
        for qa in character_data:
            answers = qa.get("answer", {})
            true_answer = answers.get("true")
            false_answer = answers.get("false")
            
            # 检查是否匹配正确答案
            if true_answer in (cleaned_answer1, cleaned_answer2):
                # 检查是否匹配错误答案
                if false_answer in (cleaned_answer1, cleaned_answer2):
                    # 如果两个答案都匹配，返回正确答案对应的原始字符串
                    if cleaned_answer1 == true_answer:
                        return answer1
                    else:
                        return answer2
        
        # 如果没有任何问题匹配这两个答案
        raise ValueError(f"在角色 '{character}' 中没有找到匹配这两个答案的问题")
