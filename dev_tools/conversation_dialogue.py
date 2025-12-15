#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NIKKE 对话数据更新脚本 - API 版本
放置在 dev_tools/conversation_dialogue.py
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# 文件路径配置
BASE_DIR = Path(__file__).parent.parent
DIALOGUE_JSON_PATH = BASE_DIR / 'module' / 'conversation' / 'dialogue.zh-CN.json'

# 网站配置
NIKKE_LIST_URL = 'https://www.gamekee.com/nikke/second/64581'
NIKKE_API_BASE = 'https://nikke.gamekee.com/v1/content/detail'

# 请求头配置
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

GAME_HEADERS = {**HEADERS, 'game-alias': 'nikke'}

# 创建 session
session = requests.Session()

# 涉及到的正则模式
CONTENT_ID_PATTERN = re.compile(r'/nikke/tj/(\d+)\.html')
QUESTION_PATTERN = re.compile(r'^问题\d+')
DOTS_PATTERN = re.compile(r'[\.·]{3,}')


def load_existing_dialogue():
    """加载现有的对话数据"""
    if DIALOGUE_JSON_PATH.exists():
        with DIALOGUE_JSON_PATH.open('r', encoding='utf-8') as f:
            data = json.load(f)
            return data
    return dict()


def save_dialogue(dialogue_data):
    """保存对话数据到 JSON 文件，保持顺序"""
    # 确保目录存在
    DIALOGUE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 保存 JSON（带缩进，方便阅读）
    # python 3.10中，dict保证有序
    with DIALOGUE_JSON_PATH.open('w', encoding='utf-8') as f:
        json.dump(dialogue_data, f, ensure_ascii=False, indent=2)

    print(f'✓ 数据已保存到 {DIALOGUE_JSON_PATH}')


def get_nikke_list(limit=None):
    """
    获取 NIKKE 角色列表

    Args:
        limit: 限制获取数量，None 表示获取全部

    Returns:
        list: [{name, content_id}, ...]
    """
    print(f'正在访问 {NIKKE_LIST_URL}')

    try:
        response = session.get(NIKKE_LIST_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'

        soup = BeautifulSoup(response.text, 'lxml')

        # 查找包含 nikke-item-group 的 div
        nikke_group = soup.find('div', class_=lambda x: x and 'nikke-item-group' in x)

        if not nikke_group:
            print('警告: 未找到 nikke-item-group 元素')
            return []

        # 查找所有带有 report-id 属性的元素，自动适配 a/div/其他标签
        items = nikke_group.find_all(attrs={'report-id': True}, limit=limit)

        nikke_list = []
        for item in items:
            title = item.get('title', '').strip()
            content_id = item.get('report-id', '').strip()

            if not title or not content_id:
                continue

            nikke_list.append({'name': title, 'content_id': content_id})
            print(f'  找到角色: {title} (ID: {content_id})')

        print(f'✓ 共找到 {len(nikke_list)} 个角色')
        return nikke_list

    except Exception as e:
        print(f'获取角色列表失败: {e}')
        import traceback

        traceback.print_exc()
        return []


def parse_label_value(group: list):
    """解析 label 和 value，防止缺失导致的异常"""
    label = group[0].get('value', '').strip() if len(group) > 0 else ''
    value = group[1].get('value', '').strip() if len(group) > 1 else ''
    return label, value


def get_nikke_dialogue(content_id, nikke_name):
    """
    获取单个 NIKKE 角色的对话数据

    Args:
        content_id: 角色的 content_id
        nikke_name: 角色名称（用于日志）

    Returns:
        tuple: (nikke_name, dialogues, status)
    """
    url = f'{NIKKE_API_BASE}/{content_id}'
    print(f'  正在获取 {nikke_name} 的对话数据...')

    try:
        response = session.get(url, headers=GAME_HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        # 检查响应格式
        if 'data' not in data or 'content_json' not in data['data']:
            print(f'  警告: {nikke_name} 的 API 响应格式不正确')
            return nikke_name, None, 'error'

        # 解析 content_json
        content_json = json.loads(data['data']['content_json'])
        base_data = content_json.get('baseData', [])

        if not base_data:
            print(f'  警告: {nikke_name} 没有 baseData')
            return nikke_name, None, 'error'

        base_data = [x for x in base_data if x and len(x) >= 2]

        # 检查名称是否相同
        index = next(i for i, d in enumerate(base_data) if d[0].get('value', '') == '角色名称')
        label, value = parse_label_value(base_data[index])
        if label == '角色名称' and value != nikke_name:
            print(f'  警告: 角色名称不匹配，使用 {value} 覆盖 {nikke_name}')
            nikke_name = value

        # 从index开始，每3个为一组进行解析，提取对话数据
        dialogues = []
        index = next(i for i, d in enumerate(base_data[index:]) if d[0].get('value', '').startswith('问题'))
        while 1:
            label, value = parse_label_value(base_data[index])
            if not QUESTION_PATTERN.match(label):
                break

            question = clean_text(value)
            for j in (index + 1, index + 2):
                label, value = parse_label_value(base_data[j])
                if label == '100好感度':
                    answer_false = clean_text(value)
                elif label == '120好感度':
                    answer_true = clean_text(value)
            index += 3

            if not (question or answer_false or answer_true):
                continue

            dialogues.append({'question': question, 'answer': {'false': answer_false, 'true': answer_true}})

        # 验证数据完整性
        dialogue_count = len(dialogues)

        if dialogue_count == 0:
            print(f'  ⊘ {nikke_name} 没有对话数据，跳过')
            return nikke_name, None, 'skip'

        if dialogue_count < 20:
            print(f'  ⏳ {nikke_name} 对话数据不完整 ({dialogue_count}/20)，等待')
            return nikke_name, None, 'waiting'

        if dialogue_count > 20:
            print(f'  警告: {nikke_name} 的对话数量超过 20 ({dialogue_count})')

        print(f'  ✓ {nikke_name} 获取成功，共 {dialogue_count} 条对话')
        return nikke_name, dialogues, 'success'

    except Exception as e:
        print(f'  ✗ 获取 {nikke_name} 的对话数据失败: {e}')
        import traceback

        traceback.print_exc()
        return nikke_name, None, 'error'


def clean_text(text):
    """
    清理文本内容

    - 删除 \n 和 \r
    - 将 ...... 替换为 ……
    - 删除 AccountDataNickName
    """
    if not text:
        return ''

    # 删除换行符
    text = text.replace('\\n', '').replace('\\r', '')
    text = text.replace('\n', '').replace('\r', '')

    # 替换省略号
    def repl_dot(match):
        dots = match.group(0)
        count = len(dots) // 3
        return '…' * count

    text = DOTS_PATTERN.sub(repl_dot, text)

    # 删除 AccountDataNickName
    text = text.replace('{AccountData.NickName}', '').replace("'", '')

    # 去除首尾空白
    text = text.strip()

    return text


def compare_dialogues(old_dialogues, new_dialogues):
    """
    比对两组对话数据是否相同

    Args:
        old_dialogues: 旧的对话列表
        new_dialogues: 新的对话列表

    Returns:
        bool: True 表示相同，False 表示不同
    """
    if not old_dialogues or not new_dialogues:
        return False

    if len(old_dialogues) != len(new_dialogues):
        return False

    # 逐条比对
    for old, new in zip(old_dialogues, new_dialogues):
        if old.get('question') != new.get('question'):
            return False

        old_answer = old.get('answer', {})
        new_answer = new.get('answer', {})

        if old_answer.get('true') != new_answer.get('true'):
            return False

        if old_answer.get('false') != new_answer.get('false'):
            return False

    return True


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='更新 NIKKE 对话数据')
    parser.add_argument('--limit', type=int, default=None, help='限制处理的角色数量（默认：全部）')
    parser.add_argument('--force', action='store_true', help='强制更新所有角色（不进行内容比对）')
    args = parser.parse_args()

    print('=' * 60)
    print('NIKKE 对话数据更新脚本')
    print('=' * 60)

    # 1. 加载现有对话数据
    existing_dialogue = load_existing_dialogue()
    print(f'当前已有 {len(existing_dialogue)} 个角色的对话数据')

    # 2. 获取角色列表
    nikke_list = get_nikke_list(limit=args.limit)

    if not nikke_list:
        print('未获取到角色列表，退出')
        return

    # 3. 处理每个角色
    updated_count = 0
    unchanged_count = 0
    skipped_list = []  # 对话数量为 0 的角色
    waiting_list = []  # 对话数量 >0 <20 的角色
    error_list = []  # 其他错误的角色
    added_names = []  # 新增的角色名称
    updated_names = []  # 更新的角色名称

    # 倒序，按实装日期从老到新处理
    for i, nikke in enumerate(reversed(nikke_list), 1):
        nikke_name = nikke['name']
        content_id = nikke['content_id']

        print(f'\n[{i}/{len(nikke_list)}] 处理角色: {nikke_name} (ID: {content_id})')

        # 等待一段时间避免请求过快
        if i > 1:
            time.sleep(1)

        # 获取对话数据
        nikke_name, new_dialogues, status = get_nikke_dialogue(content_id, nikke_name)

        # 处理不同状态
        if status == 'skip':
            skipped_list.append(nikke_name)
            continue

        if status == 'waiting':
            waiting_list.append(nikke_name)
            continue

        if status == 'error':
            error_list.append(nikke_name)
            continue

        if not new_dialogues:
            error_list.append(nikke_name)
            print(f'  ✗ {nikke_name} 处理失败')
            continue

        # 检查是否需要更新
        is_new = nikke_name not in existing_dialogue

        if not is_new and not args.force:
            old_dialogues = existing_dialogue[nikke_name]

            # 比对对话内容
            if compare_dialogues(old_dialogues, new_dialogues):
                print(f'  = {nikke_name} 对话内容未变化，跳过')
                unchanged_count += 1
                continue
            else:
                print(f'  ⟳ {nikke_name} 检测到对话内容变化，更新')

        # 更新数据 - 新角色追加到最前面
        if is_new:
            # python 3.10中，dict保证有序
            existing_dialogue = {nikke_name: new_dialogues, **existing_dialogue}
            added_names.append(nikke_name)
        else:
            # 已存在角色：直接更新
            existing_dialogue[nikke_name] = new_dialogues
            updated_names.append(nikke_name)

        updated_count += 1
        print(f'  ✓ {nikke_name} 处理完成')

    # 4. 保存数据
    if updated_count > 0:
        save_dialogue(existing_dialogue)

        # 保存更新的角色名称到文件，供 GitHub Actions 使用
        update_info_path = BASE_DIR / '.github' / 'updated_nikke.txt'
        update_info_path.parent.mkdir(parents=True, exist_ok=True)
        with update_info_path.open('w', encoding='utf-8') as f:
            if added_names:
                f.write(f'新增：{", ".join(added_names)}\n')
            if updated_names:
                f.write(f'更新：{", ".join(updated_names)}\n')
        print(f'✓ 更新信息已保存到 {update_info_path}')
    else:
        print('\n没有需要更新的数据')

    # 5. 打印统计信息
    print('\n' + '=' * 60)
    print('更新完成')
    print(f'  新增: {len(added_names)} 个角色')
    print(f'  更新: {len(updated_names)} 个角色')
    print(f'  内容未变: {unchanged_count} 个角色')
    print(f'  跳过(无数据): {len(skipped_list)} 个角色')
    print(f'  等待(不完整): {len(waiting_list)} 个角色')
    print(f'  错误: {len(error_list)} 个角色')
    print(f'  总计: {len(existing_dialogue)} 个角色')

    # 输出详细列表
    if added_names:
        print(f'\n新增的角色: {", ".join(added_names)}')

    if updated_names:
        print(f'\n更新的角色: {", ".join(updated_names)}')

    if skipped_list:
        print(f'\n跳过的角色 (对话数=0): {", ".join(skipped_list)}')

    if waiting_list:
        print(f'\n等待完善的角色 (0<对话数<20): {", ".join(waiting_list)}')

    if error_list:
        print(f'\n错误的角色: {", ".join(error_list)}')

    print('=' * 60)


if __name__ == '__main__':
    main()
