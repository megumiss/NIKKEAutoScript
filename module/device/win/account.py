import base64
import os
from module.logger import log

data_dir = "settings/accounts"
xor_key = "TI4ftRSDaP63kBxxoLoZ5KpVmRBz00JikzLNweryzZ4wecWJxJO9tbxlH9YDvjAr"

if not os.path.exists(data_dir):
    os.makedirs(data_dir)

ACCOUNT_FILE = os.path.join(data_dir, "account.acc")


def save_account(account_name: str, account_pass: str):
    """
    保存账号和密码（文件加密）
    """
    encrypted_text = xor_encrypt_to_base64(account_name + "," + account_pass)
    with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
        f.write(encrypted_text)
    log.info("账号信息已保存到文件")


def load_account() -> (str, str):
    """
    读取账号和密码
    """
    if not os.path.exists(ACCOUNT_FILE):
        log.warning("账号文件不存在")
        return None, None
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        encrypted_text = f.read().strip()
    decrypted_text = xor_decrypt_from_base64(encrypted_text)
    try:
        account_name, account_pass = decrypted_text.split(",", 1)
    except ValueError:
        log.error("账号文件格式错误")
        return None, None
    return account_name, account_pass


def xor_encrypt_to_base64(plaintext: str) -> str:
    secret_key = xor_key
    plaintext_bytes = plaintext.encode("utf-8")
    key_bytes = secret_key.encode("utf-8")

    encrypted_bytes = bytearray()
    for i in range(len(plaintext_bytes)):
        byte_plaintext = plaintext_bytes[i]
        byte_key = key_bytes[i % len(key_bytes)]
        encrypted_byte = byte_plaintext ^ byte_key
        encrypted_bytes.append(encrypted_byte)

    base64_encoded = base64.b64encode(encrypted_bytes).decode("utf-8")
    return base64_encoded


def xor_decrypt_from_base64(encrypted_base64: str) -> str:
    secret_key = xor_key
    encrypted_bytes = base64.b64decode(encrypted_base64.encode("utf-8"))
    key_bytes = secret_key.encode("utf-8")

    decrypted_bytes = bytearray()
    for i in range(len(encrypted_bytes)):
        byte_encrypted = encrypted_bytes[i]
        byte_key = key_bytes[i % len(key_bytes)]
        decrypted_byte = byte_encrypted ^ byte_key
        decrypted_bytes.append(decrypted_byte)

    decrypted_str = decrypted_bytes.decode("utf-8")
    return decrypted_str
