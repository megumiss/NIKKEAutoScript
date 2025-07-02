import json
from typing import Any, Dict, List
from module.logger import logger
from module.ocr.ocr import Digit


class ArenaBase:
    def opponent_info(self, area: tuple, field_name: str) -> int:
        letter = self.FIELD_LETTERS[field_name]
        OPPONENT_INFO = Digit([area], name='OPPONENT_INFO', letter=letter, threshold=128, lang='cnocr_num')
        return int(OPPONENT_INFO.ocr(self.device.image))

    def opponents_data(self) -> List[Dict[str, Any]]:
        """获取对手数据"""
        results = []
        for group_idx, group_config in enumerate(self.coordinate_config, start=1):
            group_result = {
                'id': group_idx,  # 对手位置序号（1=第一位，2=第二位，3=第三位）
                'data': {
                    'Power': self.opponent_info(group_config['Power'], 'Power'),
                    'CommanderLevel': self.opponent_info(group_config['CommanderLevel'], 'CommanderLevel'),
                    'SynchroLevel': self.opponent_info(group_config['SynchroLevel'], 'SynchroLevel'),
                    'Ranking': self.opponent_info(group_config['Ranking'], 'Ranking'),
                },
            }
            logger.info(f'Find opponent {group_idx}: {group_result["data"]}')
            results.append(group_result)
        return results

    def select_strategy(self, reversion=False) -> Dict:
        """根据选择策略返回相应的对手"""
        all_opponents = self.opponents_data()
        weights = json.loads(self.config.OpponentSelection_SortingWeight)

        # 提取原始数据
        dimensions = {
            'Power': [opp['data']['Power'] for opp in all_opponents],
            'CommanderLevel': [opp['data']['CommanderLevel'] for opp in all_opponents],
            'SynchroLevel': [opp['data']['SynchroLevel'] for opp in all_opponents],
            'Ranking': [opp['data']['Ranking'] for opp in all_opponents],
        }
        # 归一化处理

        normalized = {k: self._normalize(v) for k, v in dimensions.items()}
        if reversion:
            # pjjc时倒序，数值越低得分越高
            normalized['Ranking'] = [1 - val for val in normalized['Ranking']]
        # 计算综合得分
        for i, opp in enumerate(all_opponents):
            score = sum(
                normalized[dim][i] * weights[dim] for dim in ['Power', 'CommanderLevel', 'SynchroLevel', 'Ranking']
            )
            opp['score'] = score

        sorted_data = sorted(all_opponents, key=lambda x: x['score'], reverse=True)

        # 选择策略
        if self.config.OpponentSelection_SelectionStrategy == 'Max':
            return sorted_data[0]
        elif self.config.OpponentSelection_SelectionStrategy == 'Min':
            return sorted_data[-1]
        elif self.config.OpponentSelection_SelectionStrategy == 'Middle':
            return sorted_data[len(sorted_data) // 2] if sorted_data else None

    def _normalize(self, values: List[float]) -> List[float]:
        """数据归一化到[0,1]区间（处理全等值情况）"""
        min_val = min(values)
        max_val = max(values)
        if max_val == min_val:
            return [0.5] * len(values)  # 所有值相同时返回中性值
        return [(v - min_val) / (max_val - min_val) for v in values]
