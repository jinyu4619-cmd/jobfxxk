# -*- coding: utf-8 -*-
"""敏感信息加密存储（Windows DPAPI）

用当前 Windows 账户的凭据加解密，不产生任何密钥文件。
文件拷到别的电脑、或换个 Windows 账户，都解不开。

用法:
  echo {json} | python scripts/secure_store.py save    # 加密保存（从 stdin 读 JSON）
  python scripts/secure_store.py load                  # 解密输出（仅本机本人可用）
  python scripts/secure_store.py keys                  # 只列字段名，不显示值
  python scripts/secure_store.py delete                # 删除
  python scripts/secure_store.py status                # 是否存在
"""
import ctypes, json, os, sys
from ctypes import wintypes

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import DATA_DIR
STORE = os.path.join(DATA_DIR, 'sensitive.bin')

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(data: bytes) -> bytes:
    blob_in, _keep = _make_blob(data)
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError('加密失败（CryptProtectData）')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def unprotect(data: bytes) -> bytes:
    blob_in, _keep = _make_blob(data)
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError('解密失败：这份文件可能不是本机本账户加密的')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save(obj: dict):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    enc = protect(raw)
    with open(STORE, 'wb') as f:
        f.write(enc)
    # 收紧权限：只有当前用户可读写
    try:
        import subprocess
        subprocess.run(['icacls', STORE, '/inheritance:r',
                        '/grant:r', f'{os.environ.get("USERNAME")}:F'],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    print(f'已加密保存 {len(obj)} 个字段 → {STORE}')
    print('字段：' + '、'.join(obj.keys()))


def load() -> dict:
    if not os.path.exists(STORE):
        return {}
    with open(STORE, 'rb') as f:
        return json.loads(unprotect(f.read()).decode('utf-8'))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'

    if cmd == 'save':
        text = sys.stdin.read()
        obj = json.loads(text)
        save(obj)

    elif cmd == 'load':
        data = load()
        if not data:
            print('{}')
        else:
            print(json.dumps(data, ensure_ascii=False, indent=1))

    elif cmd == 'keys':
        data = load()
        if not data:
            print('（没有保存任何敏感信息）')
        else:
            print('已保存字段：' + '、'.join(data.keys()))

    elif cmd == 'delete':
        if os.path.exists(STORE):
            os.remove(STORE)
            print('已删除敏感信息文件')
        else:
            print('本来就没有')

    elif cmd == 'status':
        if os.path.exists(STORE):
            try:
                n = len(load())
                print(f'存在，{n} 个字段，{os.path.getsize(STORE)} 字节')
            except Exception as e:
                print('文件存在但解不开：' + str(e)[:80])
        else:
            print('未保存任何敏感信息')

    else:
        print(__doc__)


if __name__ == '__main__':
    main()
