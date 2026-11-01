# -*- coding: utf-8 -*-
"""统一的路径解析：代码和自己的数据分开放

配置查找顺序：
  1. 环境变量 JOBFXXK_CONFIG 指定的路径
  2. 与仓库平级的 jobfxxk-data/config.json      ← 自己 clone 仓库时最常见
  3. 用户主目录下的 jobfxxk-data/config.json    ← 装成 skill 时的规范位置
  4. 仓库内的 config.json                       ← 兼容旧布局

为什么要有第 3 条：装成 skill 之后，代码会落在应用托管的目录里，
那个目录随版本更新会被替换。数据要是跟着代码走，升级一次就没了。
"""
import json, os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPT_DIR)
HOME = os.path.expanduser('~')
SIBLING_DATA = os.path.join(os.path.dirname(REPO), 'jobfxxk-data')
HOME_DATA = os.path.join(HOME, 'jobfxxk-data')


def _config_candidates():
    env = os.environ.get('JOBFXXK_CONFIG')
    if env:
        yield env
    data_env = os.environ.get('JOBFXXK_DATA')
    if data_env:
        yield os.path.join(data_env, 'config.json')
        return
    yield os.path.join(SIBLING_DATA, 'config.json')
    yield os.path.join(HOME_DATA, 'config.json')
    yield os.path.join(REPO, 'config.json')


CONFIG_PATH = next((p for p in _config_candidates() if p and os.path.exists(p)), None)

CFG = {}
if CONFIG_PATH:
    try:
        CFG = json.load(open(CONFIG_PATH, encoding='utf-8-sig'))
    except Exception:
        CFG = {}


def _default_data_dir():
    """没配置时数据放哪：谁先存在用谁，都不存在就用主目录下的（不落进仓库）"""
    env = os.environ.get('JOBFXXK_DATA')
    if env:
        return env
    if os.path.isdir(SIBLING_DATA):
        return SIBLING_DATA
    if os.path.isdir(HOME_DATA):
        return HOME_DATA
    return HOME_DATA


DATA_DIR = os.path.dirname(CONFIG_PATH) if CONFIG_PATH else _default_data_dir()


def path_of(key, default_rel):
    """从配置里取路径；没有就退回数据目录下的默认位置"""
    v = (CFG.get(key) or '').replace('/', os.sep)
    return v if v else os.path.join(DATA_DIR, default_rel)


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR
