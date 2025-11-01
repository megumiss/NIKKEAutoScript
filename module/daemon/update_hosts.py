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
            logger.error(f'Unknown action type: {action}')
            return

        if action == 'Add' and (not new_hosts_block or not str(new_hosts_block).strip()):
            logger.error('No valid hosts content provided, skipping update.')
            return

        # 确保是字符串
        new_hosts_block = str(new_hosts_block or '')

        # 安全检查
        if not os.path.exists(hosts_path):
            logger.error('Hosts file not found, please confirm system path.')
            return

        # 备份原始文件
        shutil.copy2(hosts_path, backup_path)
        logger.info(f'Backed up original hosts file to: {backup_path}')

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
                logger.info('Deleted NKAS section.')
            else:
                logger.info('No NKAS section found, nothing to delete.')
        elif action == 'Add':
            # 仅保留未注释的行
            filtered_lines = []
            for line in new_hosts_block.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    filtered_lines.append(line)

            if not filtered_lines:
                logger.warning('All lines are commented or empty, no hosts entries written.')
                return

            new_hosts_block = '\n'.join(filtered_lines)

            # 生成新的段落
            new_block = f'{start_tag}\n{new_hosts_block.strip()}\n{end_tag}\n'

            # 判断是否已有 NKAS 段落
            if start_tag in content and end_tag in content:
                pattern = re.compile(rf'{start_tag}.*?{end_tag}', re.DOTALL)
                content = re.sub(pattern, new_block.strip(), content)
                act = 'Replaced'
            else:
                if not content.endswith('\n'):
                    content += '\n'
                content += '\n' + new_block
                act = 'Appended'

            logger.info(f'{act} NKAS section in hosts file.')

        # 写回文件
        with open(hosts_path, 'w', encoding='utf-8') as f:
            f.write(content.strip() + '\n')

        # 输出修改后的 hosts 文件内容
        logger.info('Current hosts file content:\n' + '-' * 40)
        with open(hosts_path, 'r', encoding='utf-8') as f:
            logger.info(f.read())
        logger.info('-' * 40)


if __name__ == '__main__':
    b = UpdateHosts('nkas', task='UpdateHosts')
    b.run()
