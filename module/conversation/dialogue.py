import json
import random
import re
from module.base.utils import (
    remove_punctuation,
)
from functools import cached_property
from pathlib import Path
from typing import Callable, List, Dict, Any
from difflib import SequenceMatcher

class Dialogue:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self._raw_data = None
    
    @property
    def raw_data(self) -> Dict[str, Any]:
        """加载原始JSON数据"""
        if self._raw_data is None:
            if not self.file_path.exists():
                print(f"文件不存在: {self.file_path}")
                self._raw_data = {}
                return self._raw_data
            
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self._raw_data = json.load(f)
            except json.JSONDecodeError:
                self._raw_data = {}
            except Exception as e:
                self._raw_data = {}
        
        return self._raw_data
    
    @cached_property
    def dialogue_data(self) -> Dict[str, Any]:
        """处理后的数据（仅处理角色名和问题，保留答案原始格式）"""
        def process_data(obj: Any) -> Any:
            """递归处理数据结构"""
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
                # 保留原始字符串
                return obj
            else:
                return obj
        
        return process_data(self.raw_data)
    
    def get_answer_list(self, character: str, correct: bool) -> List[str]:
        """
        获取指定角色的所有答案
        """
        # 删除角色名称中的字符
        cleaned_character = remove_punctuation(character)
        character_data = self.dialogue_data.get(cleaned_character)
        if not character_data:
            return []
        
        answers = []
        for qa in character_data:
            answer_dict = qa.get("answer", {})
            if correct:
                correct_answer = answer_dict.get("true")
                if correct_answer:
                    # 处理单个字符串或多个答案的情况
                    if isinstance(correct_answer, list):
                        answers.extend(correct_answer)
                    else:
                        answers.append(correct_answer)
            else:
                wrong_answer = answer_dict.get("false")
                if wrong_answer:
                    # 处理单个字符串或多个答案的情况
                    if isinstance(wrong_answer, list):
                        answers.extend(wrong_answer)
                    else:
                        answers.append(wrong_answer)
        
        return answers

    @staticmethod
    def similarity_difflib(str1: str, str2: str) -> float:
        '''计算两个字符串的相似度'''
        return SequenceMatcher(None, str1, str2).ratio()

    def get_answer(self, character: str, answer_list: List[str]) -> str:
        """
        返回正确答案，AI写的
        """
        if not answer_list:
            return ""
        
        # 获取所有正确答案和错误答案
        correct_answers = self.get_answer_list(character, True)
        wrong_answers = self.get_answer_list(character, False)
        
        # 策略1: 直接匹配正确答案
        for candidate in answer_list:
            if candidate in correct_answers:
                return candidate
        
        # 策略2: 错误答案排除法
        # 当其中一个在错误答案列表中时，选择其他选项
        for candidate in answer_list:
            if candidate in wrong_answers:
                other_answers = [a for a in answer_list if a != candidate and a not in wrong_answers]
                if other_answers:
                    return random.choice(other_answers)
        
        # 策略3: 综合相似度匹配策略
        candidate_scores = []
        for candidate in answer_list:
            # 1. 计算与所有正确答案的最高相似度
            max_correct_similarity = max(
                (self.similarity_difflib(candidate, correct_answer) 
                 for correct_answer in correct_answers),
                default=0.0
            )
            
            # 2. 计算与所有错误答案的最高相似度
            max_wrong_similarity = max(
                (self.similarity_difflib(candidate, wrong_answer) 
                 for wrong_answer in wrong_answers),
                default=0.0
            )
            
            # 3. 计算综合可信度得分
            confidence_score = max_correct_similarity - (0.5 * max_wrong_similarity)
            candidate_scores.append((candidate, confidence_score))
        
        # 按可信度得分降序排序
        candidate_scores.sort(key=lambda x: x[1], reverse=True)
        if candidate_scores:
            best_candidate, best_score = candidate_scores[0]
            if best_score > 0.5:
                return best_candidate
        
        return random.choice(answer_list)