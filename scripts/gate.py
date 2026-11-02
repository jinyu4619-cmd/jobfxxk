# -*- coding: utf-8 -*-
"""阶段闸门：进关键阶段之前先验一遍，不通过就拦住

为什么要有这个：规则写在文档里靠模型自觉，而自觉会失效——
这两天真实发生过「没跑微调就去填表」「上传简历后字段被清空没人发现」
「连点五次卡片开出五个标签」。文档管不住的事，让脚本管。

用法:
  python scripts/gate.py 4      # 进阶段 4（找岗）前
  python scripts/gate.py 6      # 进阶段 6（微调）前
  python scripts/gate.py 8 --version "Babycare 线上渠道运营"
  python scripts/gate.py 10

输出 {"ok": bool, "phase": "6", "checks": [{name, ok, detail, fix}]}
不通过时每一条 fix 都能直接念给用户。
"""
import argparse, json, os, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

USER = os.path.join(DATA_DIR, 'user')
RESUME_DIR = (CFG.get('resume_dir') or '').replace('/', os.sep)


def p(v):
    return (v or '').replace('/', os.sep)


def run_py(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    return r.stdout, r.stderr, r.returncode


def check(name, ok, detail='', fix=''):
    return dict(name=name, ok=bool(ok), detail=detail, fix=fix if not ok else '')


def safe_name(version):
    """和 lint_draft.py 用同一套文件名规则，否则两边对不上"""
    import re
    return re.sub(r'[\\/:*?"<>|\s]+', '_', version).strip('_') or '未命名'


# ---------- 阶段 4：找岗前 ----------

def gate4():
    checks = []
    out, err, rc = run_py('profile.py', 'check')
    try:
        d = json.loads(out)
    except Exception:
        d = {'ok': False, 'issues': [{'kind': 'error', 'detail': (err or out)[:120]}]}
    blocking = [i for i in d.get('issues', []) if i['kind'] in
                ('missing_field', 'unconfirmed_exp', 'not_reviewed', 'privacy_mode_not_set')]
    checks.append(check('信息库可用', not blocking,
                        '；'.join(i['detail'] for i in blocking[:4]),
                        '先把信息库补齐：python scripts/profile.py missing'))

    prof = {}
    try:
        prof = json.load(open(os.path.join(USER, 'profile.json'), encoding='utf-8-sig'))
    except Exception:
        pass
    direction = ((prof.get('intent') or {}).get('directions') or '').strip()
    checks.append(check('方向已定', bool(direction),
                        direction or '还没问过用户想投什么',
                        '先跑 python scripts/directions.py，把方向念给用户确认，'
                        '再写进 profile.json 的 intent.directions'))

    cache = p(CFG.get('job_cache')) or os.path.join(DATA_DIR, 'data', 'jobs.json')
    ok_cache, detail = False, '岗位缓存不存在'
    if os.path.exists(cache):
        try:
            d = json.load(open(cache, encoding='utf-8-sig'))
            rows = d.get('rows') or []
            translated = d.get('translated')
            ok_cache = bool(rows) and bool(translated)
            detail = f'{len(rows)} 条' + ('，已翻译' if translated else '，**还没翻译**')
        except Exception as e:
            detail = '读不了：' + str(e)[:60]
    checks.append(check('岗位库可用', ok_cache, detail,
                        '跑 node scripts/sync_jobs.cjs --only=jobs 和 python scripts/translate_jobs.py'))
    return checks


# ---------- 阶段 6：微调前 ----------

def gate6():
    checks = []
    docx, pdf = p(CFG.get('master_docx')), p(CFG.get('master_pdf'))
    checks.append(check('母版 Word 在', docx and os.path.exists(docx), docx or '没配',
                        'python scripts/setup.py --apply set_resume --docx <路径>'))
    checks.append(check('母版 PDF 在', pdf and os.path.exists(pdf), pdf or '没配',
                        '不给 PDF 也行，跑 set_resume 时只传 --docx，脚本会自己导'))

    stale = ''
    if docx and pdf and os.path.exists(docx) and os.path.exists(pdf):
        if os.path.getmtime(docx) - os.path.getmtime(pdf) > 60:
            stale = 'Word 比 PDF 新，标定是旧的'
    checks.append(check('docx / pdf 同步', not stale, stale,
                        '先用 Word 把 docx 另存为 PDF，再重跑 make_anchors'))

    anchors = p(CFG.get('anchors')) or os.path.join(USER, '锚点表.json')
    ok_a, detail = False, '锚点表不存在'
    if os.path.exists(anchors):
        try:
            a = json.load(open(anchors, encoding='utf-8-sig'))
            n = len(a.get('slots') or [])
            ok_a = n > 0
            detail = f'{n} 个槽位，每行 {a.get("capacity")} 字'
        except Exception as e:
            detail = '读不了：' + str(e)[:60]
    checks.append(check('锚点表可用', ok_a, detail, 'python scripts/make_anchors.py'))
    return checks


# ---------- 阶段 8：填表前（最容易出事的一步） ----------

def gate8(version):
    checks = []
    if not version:
        checks.append(check('给了版本名', False, '没告诉我要投哪一版',
                            '用 --version "公司 岗位" 指定，和 fill_engine --out 时一致'))
        return checks

    pdf_dir = os.path.join(RESUME_DIR, 'pdf') if RESUME_DIR else ''
    docx_dir = os.path.join(RESUME_DIR, 'docx') if RESUME_DIR else ''
    name = CFG.get('name') or ''
    stem = f'{name} - {version}'
    docx = os.path.join(docx_dir, stem + '.docx') if docx_dir else ''
    pdf = os.path.join(pdf_dir, stem + '.pdf') if pdf_dir else ''

    checks.append(check('微调版 docx 在', docx and os.path.exists(docx), docx or '路径没配',
                        f'先跑 fill_engine：python scripts/fill_engine.py 草稿.json --out "{version}"'))
    checks.append(check('微调版 pdf 在', pdf and os.path.exists(pdf), pdf or '路径没配',
                        f'同上，--out "{version}" 会同时产出 docx 和 pdf'))

    # 版式验收：拿母版 PDF 和这一版逐项比
    if pdf and os.path.exists(pdf):
        out, err, rc = run_py('verify.py', 'layout', pdf)
        try:
            v = json.loads(out)
        except Exception:
            v = {'ok': False, 'issues': [(err or out)[:100]]}
        checks.append(check('版式验收通过', v.get('ok'),
                            '；'.join(str(x) for x in v.get('issues', [])[:3]),
                            '版式动了就不能用这版投，回去改字数重新生成，或者换回原版'))

    # 改动校验：这一版生成时，数字/日期/奖项/夸大词是不是都过了关
    rec_dir = os.path.join(DATA_DIR, 'user', 'lint')
    rec = os.path.join(rec_dir, safe_name(version) + '.json')
    ok_lint, detail = False, '这一版没有校验记录，说明它不是走 fill_engine 生成的'
    if os.path.exists(rec):
        try:
            d = json.load(open(rec, encoding='utf-8-sig'))
            ok_lint = bool(d.get('ok'))
            detail = f'{d.get("edits")} 条改动，拦住 {d.get("blocked")} 条，{d.get("at", "")}'
        except Exception as ex:
            detail = '记录读不了：' + str(ex)[:60]
    checks.append(check('改动校验通过', ok_lint, detail,
                        f'重跑 fill_engine（它自己会校验）：'
                        f'python scripts/fill_engine.py 草稿.json --out "{version}"'))
    return checks


# ---------- 阶段 10：记账前 ----------

def gate10():
    checks = []
    tracker = p(CFG.get('tracker')) or os.path.join(DATA_DIR, '投递台账.md')
    d = os.path.dirname(tracker) or '.'
    writable = os.path.isdir(d) and os.access(d, os.W_OK)
    checks.append(check('台账目录可写', writable, d,
                        '把 config.tracker 指到一个你能写的目录'))
    out, err, rc = run_py('verify.py', 'tracker')
    try:
        v = json.loads(out)
    except Exception:
        v = {'ok': True, 'issues': []}
    checks.append(check('台账结构正常', v.get('ok'),
                        '；'.join(str(x) for x in v.get('issues', [])[:3]),
                        '按 verify.py tracker 的输出修表格'))
    return checks


GATES = {'4': gate4, '6': gate6, '8': gate8, '10': gate10}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('phase', choices=sorted(GATES) + ['list'])
    ap.add_argument('--version', default='')
    a = ap.parse_args()

    if a.phase == 'list':
        print(json.dumps({'gates': {k: v.__doc__ for k, v in GATES.items()}}, ensure_ascii=False, indent=1))
        return

    checks = GATES[a.phase]() if a.phase != '8' else gate8(a.version)
    ok = all(c['ok'] for c in checks)
    print(json.dumps({'ok': ok, 'phase': a.phase, 'checks': checks}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
