import os
import base64
import getpass
import hashlib
import json  # 引入 json 模块
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
# from module.logger import logger # 假设你有一个 logger 模块，如果没有可以注释掉
import logging # 使用标准 logging 模块作为替代

# --- 如果没有自己的 logger 模块，可以使用下面的配置作为替代 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# -----------------------------------------------------------

data_dir = "config"

if not os.path.exists(data_dir):
    os.makedirs(data_dir)


def _derive_key_from_username() -> bytes:
    """
    使用 Windows 当前用户名生成 AES-256 密钥
    """
    username = getpass.getuser().encode("utf-8")  # 可能是中文
    key = hashlib.sha256(username).digest()  # 固定 32 字节
    return key


def _get_account_file(config_name: str) -> str:
    """
    根据 config_name 生成对应的存储文件路径
    """
    safe_name = "".join(c for c in config_name if c.isalnum() or c in ("_", "-"))
    return os.path.join(data_dir, f"account_{safe_name}.acc")


def save_account(config_name: str, account_name: str, account_pass: str):
    """
    保存账号和密码（AES-256 加密），内容为 JSON 格式
    """
    try:
        key = _derive_key_from_username()
        iv = os.urandom(16)  # 随机 IV

        # 将账号和密码打包成字典
        account_data = {
            "username": account_name,
            "password": account_pass
        }
        # 将字典转换为 JSON 字符串，然后编码为字节
        plaintext = json.dumps(account_data).encode("utf-8")

        # PKCS7 填充
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(plaintext) + padder.finalize()

        # AES-CBC 加密
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # 保存 (iv + ciphertext)，再 base64
        encoded = base64.b64encode(iv + ciphertext).decode("utf-8")
        acc_file = _get_account_file(config_name)
        with open(acc_file, "w", encoding="utf-8") as f:
            f.write(encoded)

        logger.info(f"账号信息已加密保存: {acc_file}")
    except Exception as e:
        logger.error(f"保存账号失败: {e}")


def load_account(config_name: str) -> (str, str):
    """
    读取并解密账号和密码（从 JSON 格式）
    """
    acc_file = _get_account_file(config_name)
    if not os.path.exists(acc_file):
        logger.warning(f"账号文件不存在: {acc_file}")
        return None, None

    try:
        key = _derive_key_from_username()

        with open(acc_file, "r", encoding="utf-8") as f:
            encoded = f.read().strip()

        data = base64.b64decode(encoded)
        iv, ciphertext = data[:16], data[16:]

        # AES-CBC 解密
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # 去填充
        unpadder = padding.PKCS7(128).unpadder()
        plaintext_bytes = unpadder.update(padded_plaintext) + unpadder.finalize()

        # 将解密后的字节解析为 JSON
        account_data = json.loads(plaintext_bytes.decode('utf-8'))
        
        # 从字典中获取用户名和密码
        account_name = account_data.get("username")
        account_pass = account_data.get("password")
        
        return account_name, account_pass
    except json.JSONDecodeError as e:
        logger.error(f"读取账号失败：文件内容不是有效的JSON格式 - {e}")
        return None, None
    except Exception as e:
        logger.error(f"读取账号失败: {e}")
        return None, None

if __name__ == "__main__":
    # 示例配置名
    config = "my_app_config"

    # 保存账号
    print("正在保存账号...")
    save_account(config, "1074176954@qq.com", "P@sxxxxxx2025")

    # 读取账号
    print("正在读取账号...")
    name, pwd = load_account(config)
    
    if name and pwd:
        print(f"成功读取账号: {name}, 密码: {pwd}")
    else:
        print("读取账号失败。")