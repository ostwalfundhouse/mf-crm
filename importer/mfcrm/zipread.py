"""Open the registrars' password-protected zip files.

CAMS uses classic zip encryption (handled by Python's zipfile). KFintech uses
WinZip AES encryption, which zipfile cannot read, so it is decrypted here with
the `cryptography` package.
"""
import hashlib
import io
import struct
import zipfile
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WrongPassword(Exception):
    pass


def is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def _aes_decrypt(data: bytes, extra: bytes, password: bytes, name: str):
    strength = method = None
    pos = 0
    while pos + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[pos:pos + 4])
        if header_id == 0x9901:
            _version, _vendor, strength, method = struct.unpack("<H2sBH", extra[pos + 4:pos + 11])
        pos += 4 + size
    if strength is None:
        raise ValueError(f"{name}: AES entry without AES header")
    salt_len = {1: 8, 2: 12, 3: 16}[strength]
    key_len = {1: 16, 2: 24, 3: 32}[strength]
    salt = data[:salt_len]
    verifier = data[salt_len:salt_len + 2]
    ciphertext = data[salt_len + 2:-10]          # last 10 bytes are the auth code
    keys = hashlib.pbkdf2_hmac("sha1", password, salt, 1000, 2 * key_len + 2)
    if keys[-2:] != verifier:
        raise WrongPassword(name)
    encryptor = Cipher(algorithms.AES(keys[:key_len]), modes.ECB()).encryptor()
    blocks = (len(ciphertext) + 15) // 16
    # WinZip AES uses counter mode with a little-endian counter starting at 1.
    stream = encryptor.update(b"".join((i + 1).to_bytes(16, "little") for i in range(blocks)))
    plain = bytes(a ^ b for a, b in zip(ciphertext, stream))
    if method == 8:
        return zlib.decompress(plain, -15)
    if method == 0:
        return plain
    raise ValueError(f"{name}: unsupported compression {method}")


def _aes_entries(data: bytes, password: bytes):
    end = data.rfind(b"PK\x05\x06")
    count, _size, offset = struct.unpack("<HII", data[end + 10:end + 20])
    pos = offset
    for _ in range(count):
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", data[pos:pos + 46])
        method, comp_size = fields[4], fields[8]
        name_len, extra_len, comment_len, local_offset = fields[10], fields[11], fields[12], fields[16]
        name = data[pos + 46:pos + 46 + name_len].decode("cp437")
        extra = data[pos + 46 + name_len:pos + 46 + name_len + extra_len]
        pos += 46 + name_len + extra_len + comment_len
        if name.endswith("/"):
            continue
        local_name_len, local_extra_len = struct.unpack("<HH", data[local_offset + 26:local_offset + 30])
        start = local_offset + 30 + local_name_len + local_extra_len
        payload = data[start:start + comp_size]
        if method == 99:
            yield name, _aes_decrypt(payload, extra, password, name)
        elif method == 8:
            yield name, zlib.decompress(payload, -15)
        else:
            yield name, payload


def unzip(data: bytes, password: str):
    """Return [(file name, bytes)] for every file in the zip."""
    pwd = password.encode()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if any(info.compress_type == 99 for info in zf.infolist()):
            return list(_aes_entries(data, pwd))
        out = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                out.append((info.filename, zf.read(info, pwd=pwd if info.flag_bits & 1 else None)))
            except RuntimeError as exc:
                if "password" in str(exc).lower():
                    raise WrongPassword(info.filename) from exc
                raise
        return out
