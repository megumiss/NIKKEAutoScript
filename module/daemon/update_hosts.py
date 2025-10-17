import os
import re
import shutil

from module.config.utils import deep_get
from module.logger import logger
from module.ui.ui import UI


class UpdateHosts(UI):
    def __init__(self, config):
        super().__init__(config, independent=True)

    def run(self):
        self.update_hosts(
            deep_get(self.config.data, keys='UpdateHosts.Hosts.Action'),
            deep_get(self.config.data, keys='UpdateHosts.Hosts.Hosts'),
        )

    @staticmethod
    def update_hosts(action='Add', new_hosts_block: str = ''):
        """
        更新 Windows hosts 文件中 NKAS 段落内容。
        - Add: 添加或更新段落（仅包含未注释的行）
        - Delete: 删除整个 NKAS 段落
        """

        hosts_path = r'C:\Windows\System32\drivers\etc\hosts'
        backup_path = hosts_path + '.bak'

        # 参数检查
        if action not in ['Add', 'Delete']:
            logger.error(f'未知操作类型：{action}')
            return

        if action == 'Add' and (not new_hosts_block or not str(new_hosts_block).strip()):
            logger.error('未提供有效的 hosts 内容，跳过更新。')
            return

        # 确保是字符串
        new_hosts_block = str(new_hosts_block or '')

        # 安全检查
        if not os.path.exists(hosts_path):
            logger.error('未找到 hosts 文件，请确认系统路径。')
            return

        # 备份原始文件
        shutil.copy2(hosts_path, backup_path)
        logger.info(f'已备份原 hosts 文件到: {backup_path}')

        # 读取当前内容
        with open(hosts_path, 'r', encoding='utf-8') as f:
            content = f.read()

        start_tag = '# ===== NIKKE BY NKAS START ====='
        end_tag = '# ===== NIKKE BY NKAS END ====='

        # 处理操作类型
        if action == 'Delete':
            if start_tag in content and end_tag in content:
                pattern = re.compile(rf'{start_tag}.*?{end_tag}\n?', re.DOTALL)
                content = re.sub(pattern, '', content).strip() + '\n'
                logger.info('已删除 NKAS 段落。')
            else:
                logger.info('未找到 NKAS 段落，无需删除。')
        elif action == 'Add':
            # 仅保留未注释的行
            filtered_lines = []
            for line in new_hosts_block.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    filtered_lines.append(line)

            if not filtered_lines:
                logger.warning('所有行均为注释或空行，未写入任何 hosts 项。')
                return

            new_hosts_block = '\n'.join(filtered_lines)

            # 生成新的段落
            new_block = f'{start_tag}\n{new_hosts_block.strip()}\n{end_tag}\n'

            # 判断是否已有 NKAS 段落
            if start_tag in content and end_tag in content:
                pattern = re.compile(rf'{start_tag}.*?{end_tag}', re.DOTALL)
                content = re.sub(pattern, new_block.strip(), content)
                act = '替换'
            else:
                if not content.endswith('\n'):
                    content += '\n'
                content += '\n' + new_block
                act = '追加'

            logger.info(f'已{act} NKAS 段落到 hosts 文件。')

        # 写回文件
        with open(hosts_path, 'w', encoding='utf-8') as f:
            f.write(content.strip() + '\n')

        # 输出修改后的 hosts 文件内容
        logger.info('当前 hosts 文件内容如下：\n' + '-' * 40)
        with open(hosts_path, 'r', encoding='utf-8') as f:
            logger.info(f.read())
        logger.info('-' * 40)


if __name__ == '__main__':
    b = UpdateHosts('nkas', task='UpdateHosts')
    b.run()
