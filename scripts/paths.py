# -*- coding: utf-8 -*-
"""统一的路径解析：skill 放在仓库里，个人数据放在仓库外面

配置查找顺序：
  1. 环境变量 JOBFXXK_CONFIG 指定的路径
  2. 与仓库平级的 jobfxxk-data/config.json   ← 推荐
  3. 仓库内的 config.json                     ← 兼容旧布局

个人数据（配置、锚点表、加密文件、岗位缓存）默认都放在与仓库平级的
jobfxxk-data/ 目录里，这样仓库文件夹本身永远是干净的，可以直接发布。
"""
import json, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPT_DIR)


def _config_candidates():
    env = os.environ.get('JOBFXXK_CONFIG')
    if env:
        yield env
    yield os.path.join(os.path.dirname(REPO), 'jobfxxk-data', 'config.json')
    yield os.path.join(REPO, 'config.json')


CONFIG_PATH = next((p for p in _config_candidates() if p and os.path.exists(p)), None)

CFG = {}
if CONFIG_PATH:
    try:
        CFG = json.load(open(CONFIG_PATH, encoding='utf-8'))
    except Exception:
        CFG = {}

# 数据目录：配置文件所在目录
DATA_DIR = os.path.dirname(CONFIG_PATH) if CONFIG_PATH else os.path.join(os.path.dirname(REPO), 'jobfxxk-data')


def path_of(key, default_rel):
    """从配置里取路径；没有就退回数据目录下的默认位置"""
    v = (CFG.get(key) or '').replace('/', os.sep)
    return v if v else os.path.join(DATA_DIR, default_rel)


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR
