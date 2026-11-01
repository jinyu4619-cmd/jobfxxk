# -*- coding: utf-8 -*-
"""敏感信息加密存储（Windows DPAPI）

用当前 Windows 账户的凭据加解密，不产生任何密钥文件。
文件拷到别的电脑、或换个 Windows 账户，都解不开。

用法:
  python scripts/secure_store.py save --file 敏感.json   # 加密保存（UTF-8 文件，推荐）
  echo {json} | python scripts/secure_store.py save      # 也支持管道，但中文可能被转成 ?????
  python scripts/secure_store.py keys                  # 只列字段名
  python scripts/secure_store.py load                  # 字段名 + 掩码值（默认，安全）
  python scripts/secure_store.py load --field 身份证号  # 取单个字段明文（会写进对话记录）
  python scripts/secure_store.py load --all            # 全部明文（慎用）
  python scripts/secure_store.py delete                # 删除
  python scripts/secure_store.py status                # 是否存在
"""
import ctypes, io, json, os, sys
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


def mask(v):
    """留头留尾，中间打码。身份证这类看一眼能认出来，又不会整串进日志。"""
    s = str(v)
    if len(s) <= 4:
        return '*' * len(s)
    return s[:2] + '*' * max(1, len(s) - 4) + s[-2:]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    args = sys.argv[2:]

    if cmd == 'save':
        if '--file' in args:
            path = args[args.index('--file') + 1]
            if not os.path.exists(path):
                sys.exit(f'找不到输入文件：{path}')
            text = io.open(path, encoding='utf-8-sig').read()
        else:
            text = sys.stdin.buffer.read().decode('utf-8-sig', errors='replace')
        try:
            obj = json.loads(text)
        except Exception as e:
            sys.exit('输入不是合法 JSON：' + str(e)[:80])
        save(obj)

    elif cmd == 'load':
        data = load()
        if not data:
            print('{}')
        elif '--all' in args:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        elif '--field' in args:
            k = args[args.index('--field') + 1]
            if k not in data:
                sys.exit(f'没有这个字段：{k}\n已存的字段：' + '、'.join(data.keys()))
            print(data[k])
        else:
            # 默认只给掩码：明文进对话记录、终端日志、模型上下文都是泄露面
            for k, v in data.items():
                print(f'{k}：{mask(v)}')
            print('\n要取某个字段的明文：load --field 字段名')

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
