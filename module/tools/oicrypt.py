"""NIKKE 启动器配置（production_gl_launcher.db）使用的 TEA/OI 加解密。

格式：1 字节随机数（低 3 位为 pad 长度）+ pad 字节随机填充 + 2 字节随机头
+ 明文 + 7 字节零填充，整体按 8 字节分组做 TEA-CBC 变体加密。
密钥来自启动器二进制，16 字节固定值。
"""

import os
import struct

DELTA = 0x9E3779B9
KEY = b'!#S!9_@%@A%`@+-$'
assert len(KEY) == 16


def tea_dec_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack('>2I', block)
    k = struct.unpack('>4I', key)
    s = 0xE3779B90
    for _ in range(16):
        v1 = (v1 - (((v0 << 4) + k[2]) ^ (v0 + s) ^ ((v0 >> 5) + k[3]))) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4) + k[0]) ^ (v1 + s) ^ ((v1 >> 5) + k[1]))) & 0xFFFFFFFF
        s = (s - DELTA) & 0xFFFFFFFF
    return struct.pack('>2I', v0, v1)


def tea_enc_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack('>2I', block)
    k = struct.unpack('>4I', key)
    s = 0
    for _ in range(16):
        s = (s + DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((v1 << 4) + k[0]) ^ (v1 + s) ^ ((v1 >> 5) + k[1]))) & 0xFFFFFFFF
        v1 = (v1 + (((v0 << 4) + k[2]) ^ (v0 + s) ^ ((v0 >> 5) + k[3]))) & 0xFFFFFFFF
    return struct.pack('>2I', v0, v1)


def _xor8(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def oi_decrypt(cipher: bytes, key: bytes = KEY) -> bytes:
    n = len(cipher)
    if n % 8 or n < 16:
        raise ValueError('bad length')
    plain = tea_dec_block(cipher[:8], key)
    pad = plain[0] & 7
    n_plain = n - pad - 10
    if n_plain < 0:
        raise ValueError('bad pad')
    pre_xor = b'\x00' * 8
    pre_crypt = cipher[:8]
    i = 8
    pos = pad + 1
    # skip 2 header bytes；pos 可能正好落在块边界上，须先判边界再前进
    for _ in range(2):
        if pos == 8:
            plain = tea_dec_block(_xor8(plain, cipher[i:i + 8]), key)
            pre_xor = pre_crypt
            pre_crypt = cipher[i:i + 8]
            i += 8
            pos = 0
        pos += 1
    out = bytearray()
    for _ in range(n_plain):
        if pos == 8:
            plain = tea_dec_block(_xor8(plain, cipher[i:i + 8]), key)
            pre_xor = pre_crypt
            pre_crypt = cipher[i:i + 8]
            i += 8
            pos = 0
        out.append(plain[pos] ^ pre_xor[pos])
        pos += 1
    return bytes(out)


def oi_encrypt(plain: bytes, key: bytes = KEY) -> bytes:
    """oi_decrypt 的逆运算。"""
    n = len(plain)
    # 总长 = 1 + pad + 2 + n + 7，必须为 8 的倍数
    pad = (8 - (n + 10) % 8) % 8
    buf = bytes([0xA0 | pad]) + os.urandom(pad + 2) + plain + b'\x00' * 7
    blocks = [buf[i:i + 8] for i in range(0, len(buf), 8)]
    # D_0 = P_0, C_0 = TEA_ENC(P_0)；D_i = P_i ^ C_{i-1}，C_i = TEA_ENC(D_i) ^ D_{i-1}
    d_prev = blocks[0]
    c_prev = tea_enc_block(d_prev, key)
    out = [c_prev]
    for block in blocks[1:]:
        d_i = _xor8(block, c_prev)
        c_i = _xor8(tea_enc_block(d_i, key), d_prev)
        out.append(c_i)
        d_prev = d_i
        c_prev = c_i
    return b''.join(out)
