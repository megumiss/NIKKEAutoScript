import json
import re

# 删除src src\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s>]+))

TARGET_CHARACTER_NAME = "伊莱格：BOOM与惊吓"
INPUT_HTML_FILE = "module\conversation\dialogue_raw.html"
OUTPUT_JSON_FILE = "module\conversation\dialogue.json"

def extract_dialogues(html_content):
    # 正则模式匹配角色名称和对话块
    character_pattern = r'([^:\n]+):?[^\n]*\n(.*?)(?=\n{2,}|\Z)'
    # 匹配对话组
    group_pattern = r'<div[^>]*class="conversation-group-list"[^>]*>(.*?)</div>\s*</div>'
    # 匹配问题
    question_pattern = r'<span[^>]*class="title-text"[^>]*>(.*?)</span>'
    # 匹配错误答案 (dialogue-100)
    false_answer_pattern = r'<div[^>]*class="dialogue-100"[^>]*>.*?<span[^>]*>(.*?)</span>'
    # 匹配正确答案 (dialogue-120)
    true_answer_pattern = r'<div[^>]*class="dialogue-120"[^>]*>.*?<span[^>]*>(.*?)</span>'
    
    dialogues_data = {}
    
    # 按角色分割内容
    character_blocks = re.findall(character_pattern, html_content, re.DOTALL)
    
    for character, block in character_blocks:
        character = character.strip()
        # 提取对话组
        groups = re.findall(group_pattern, block, re.DOTALL)
        
        character_dialogues = []
        for group in groups:
            # 提取问题
            question_match = re.search(question_pattern, group, re.DOTALL)
            if not question_match:
                continue
            question = question_match.group(1).strip()
            
            # 提取错误答案
            false_match = re.search(false_answer_pattern, group, re.DOTALL)
            false_answer = false_match.group(1).strip() if false_match else ""
            
            # 提取正确答案
            true_match = re.search(true_answer_pattern, group, re.DOTALL)
            true_answer = true_match.group(1).strip() if true_match else ""
            
            character_dialogues.append({
                "question": question,
                "answer": {
                    "false": false_answer,
                    "true": true_answer
                }
            })
        
        if character_dialogues:
            dialogues_data[character] = character_dialogues
    
    return dialogues_data

def update_json(dialogues_data, target_characters, filename):
    # 尝试读取现有JSON数据
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_data = {}
    
    # 创建更新后的数据
    updated_data = {}
    
    # 先添加目标角色（按输入顺序）
    for character in target_characters:
        if character in dialogues_data:
            updated_data[character] = dialogues_data[character]
    
    # 添加现有数据中的其他角色
    for character, dialogues in existing_data.items():
        if character not in updated_data:
            updated_data[character] = dialogues
    
    # 写入文件
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(updated_data, f, ensure_ascii=False, indent=2)

# 使用示例
if __name__ == "__main__":
    # 读取HTML文件内容
    with open(INPUT_HTML_FILE, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # 指定要处理的角色（按优先级顺序）
    target_characters = [TARGET_CHARACTER_NAME]
    
    # 提取对话数据
    dialogues_data = extract_dialogues(html_content)
    
    # 更新JSON文件
    update_json(dialogues_data, target_characters, OUTPUT_JSON_FILE)
    
    print(f"成功处理 {len(dialogues_data)} 个角色数据")