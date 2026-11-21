import json
import random
import re
from difflib import SequenceMatcher
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from module.base.utils import (
    remove_punctuation,
)


class Dialogue:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self._raw_data = None

    @property
    def raw_data(self) -> Dict[str, Any]:
        """加载原始JSON数据"""
        if self._raw_data is None:
            if not self.file_path.exists():
                print(f'文件不存在: {self.file_path}')
                self._raw_data = {}
                return self._raw_data

            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self._raw_data = json.load(f)
            except json.JSONDecodeError:
                self._raw_data = {}
            except Exception:
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
            answer_dict = qa.get('answer', {})
            if correct:
                correct_answer = answer_dict.get('true')
                if correct_answer:
                    # 处理单个字符串或多个答案的情况
                    if isinstance(correct_answer, list):
                        answers.extend(correct_answer)
                    else:
                        answers.append(correct_answer)
            else:
                wrong_answer = answer_dict.get('false')
                if wrong_answer:
                    # 处理单个字符串或多个答案的情况
                    if isinstance(wrong_answer, list):
                        answers.extend(wrong_answer)
                    else:
                        answers.append(wrong_answer)

        return answers

    @staticmethod
    def similarity_difflib(str1: str, str2: str) -> float:
        """计算两个字符串的相似度"""
        return SequenceMatcher(None, str1, str2).ratio()

    @staticmethod
    def _matches_nickname(candidate: str, expected: str) -> bool:
        placeholder = '{AccountData.NickName}'
        if placeholder not in expected:
            return False
        prefix, suffix = expected.split(placeholder, 1)
        pattern = re.escape(prefix) + r'(?P<nickname>.{1,40})' + re.escape(suffix)
        match = re.fullmatch(pattern, candidate, flags=re.DOTALL)
        return match is not None and bool(match.group('nickname').strip())

    def _answer_similarity(self, candidate: str, expected: str) -> float:
        if '{AccountData.NickName}' in expected:
            return float(self._matches_nickname(candidate, expected))
        return self.similarity_difflib(candidate, expected)

    def get_answer_index(self, character: str, answer_list: List[str]) -> Optional[int]:
        """按原有角色答案库匹配，返回原始选项下标；无法匹配时仍随机选择。"""
        if not answer_list:
            return None

        correct_answers = self.get_answer_list(character, True)
        wrong_answers = self.get_answer_list(character, False)

        # 固定答案先匹配，避免昵称占位符把普通选项误当作玩家名字。
        for index, candidate in enumerate(answer_list):
            if candidate in correct_answers:
                return index
        for index, candidate in enumerate(answer_list):
            if any(self._matches_nickname(candidate, answer) for answer in correct_answers):
                return index

        for index, candidate in enumerate(answer_list):
            if candidate in wrong_answers or any(
                self._matches_nickname(candidate, answer) for answer in wrong_answers
            ):
                other_indices = [
                    i for i, answer in enumerate(answer_list)
                    if answer != candidate and answer not in wrong_answers
                    and not any(self._matches_nickname(answer, expected) for expected in wrong_answers)
                ]
                if other_indices:
                    return random.choice(other_indices)

        candidate_scores = []
        for index, candidate in enumerate(answer_list):
            max_correct_similarity = max(
                (self._answer_similarity(candidate, answer) for answer in correct_answers), default=0.0,
            )
            max_wrong_similarity = max(
                (self._answer_similarity(candidate, answer) for answer in wrong_answers), default=0.0,
            )
            confidence_score = max_correct_similarity - (0.5 * max_wrong_similarity)
            candidate_scores.append((index, confidence_score))

        candidate_scores.sort(key=lambda item: item[1], reverse=True)
        if candidate_scores:
            best_index, best_score = candidate_scores[0]
            if best_score > 0.5:
                return best_index

        return random.choice(list(range(len(answer_list))))

    def get_answer(self, character: str, answer_list: List[str]) -> str:
        """兼容原有返回选项文字的调用。"""
        index = self.get_answer_index(character, answer_list)
        return answer_list[index] if index is not None else ''
