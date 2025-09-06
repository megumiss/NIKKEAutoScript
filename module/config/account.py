import os
import base64
import getpass
import hashlib
import json
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from module.logger import logger


data_dir = 'config'

if not os.path.exists(data_dir):
    os.makedirs(data_dir)


def _derive_key_from_username() -> bytes:
    """
    使用 Windows 当前用户名生成 AES-256 密钥
    """
    username = getpass.getuser().encode('utf-8')  # 可能是中文
    key = hashlib.sha256(username).digest()  # 固定 32 字节
    return key


def _get_account_file(config_name: str) -> str:
    """
    根据 config_name 生成对应的存储文件路径
    """
    safe_name = ''.join(c for c in config_name if c.isalnum() or c in ('_', '-'))
    return os.path.join(data_dir, f'{safe_name}.acc')


def _encrypt_field(key: bytes, value: str) -> str:
    """加密单个字段并返回 base64 编码"""
    if value is None:
        return None
    value = str(value)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode('utf-8')


def _decrypt_field(key: bytes, data: str) -> str:
    """解密单个字段"""
    if not data:
        return None
    raw = base64.b64decode(data)
    iv, ciphertext = raw[:16], raw[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext_bytes = unpadder.update(padded_plaintext) + unpadder.finalize()
    return plaintext_bytes.decode('utf-8')


def _is_masked(value) -> bool:
    """
    判断输入是否应被视为“掩码/空”，包括：
    - None
    - 空字符串（或全空白）
    - 由若干个 * 组成（任意长度）
    - 非字符串类型会先转为字符串再判断（例如 int）
    """
    if value is None:
        return True
    s = str(value).strip()
    if s == "":
        return True
    return all(ch == "*" for ch in s)


def save_account(config_name: str, account: str = None, password: str = None):
    """
    保存账号和密码，可以只传其中一个；支持追加合并保存
    """
    try:
        key = _derive_key_from_username()
        acc_file = _get_account_file(config_name)

        # 先读取已有数据（如果存在）
        if os.path.exists(acc_file):
            try:
                with open(acc_file, 'r', encoding='utf-8') as f:
                    account_data = json.load(f)
            except Exception:
                account_data = {}
        else:
            account_data = {}

        # 更新新字段（仅当传入不为 None）
        if account is not None and not _is_masked(account):
            account_data['username'] = _encrypt_field(key, account)
        if password is not None and not _is_masked(password):
            account_data['password'] = _encrypt_field(key, password)

        # 保存合并后的数据
        with open(acc_file, 'w', encoding='utf-8') as f:
            json.dump(account_data, f, ensure_ascii=False)

        logger.info(f'账号信息已加密保存: {acc_file}')
    except Exception as e:
        logger.error(f'保存账号失败: {e}')


def load_account(config_name: str) -> (str, str):
    """
    读取并解密账号和密码，可以只读取其中一个
    """
    acc_file = _get_account_file(config_name)
    if not os.path.exists(acc_file):
        logger.warning(f'账号文件不存在: {acc_file}')
        return None, None

    try:
        key = _derive_key_from_username()
        with open(acc_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)

        account = _decrypt_field(key, encrypted_data.get('username'))
        password = _decrypt_field(key, encrypted_data.get('password'))

        return account, password
    except Exception as e:
        logger.error(f'读取账号失败: {e}')
        return None, None


if __name__ == '__main__':
    config = 'nkas'

    # 第一次只保存账号
    # print('正在保存账号...')
    # save_account(config, account='10000000')

    # # 第二次追加保存密码
    # print('正在追加保存密码...')
    # save_account(config, password='xxxxxxxx')

    # 读取账号和密码
    print('正在读取账号...')
    name, pwd = load_account(config)
    if name or pwd:
        print(f'成功读取账号: {name}, 密码: {pwd}')
    else:
        print('读取账号失败。')
