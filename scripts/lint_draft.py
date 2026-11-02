# -*- coding: utf-8 -*-
"""改动校验：模型写的每一句，先过事实关和说法关，再允许落进简历

这是「三道关」的后两道（第一道是 fill_engine 的字数窗口）：
  1. 事实关：新添的数字、日期、奖项等级，必须能在你自己的材料里找到出处；
             原句里的数字被换掉、被删掉，也要报出来。
  2. 说法关：新添的夸大词（顶尖、唯一、行业第一…）直接拦；
             形容词式自评（优秀、出色、显著提升…）单独列出来提醒。

为什么要有它：文档写「不许编造」是规则，靠模型自觉；而不同模型对同一句话的
理解不一样。把「什么算编造」压成机器能judge的东西，这一步的差异就归零——
不是变小，是消失。剩下的判断（这句话该怎么写）才留给模型。

判断口径只有一条：**只看这条改动新添了什么**。原文里本来就有的词不算，
不然母版的历史包袱会一直误报。

用法:
  python scripts/lint_draft.py 草稿.json
  python scripts/lint_draft.py 草稿.json --allow "23%"
  python scripts/lint_draft.py 草稿.json --record "公司 岗位"   # 通过后落记录，gate.py 8 认
  python scripts/lint_draft.py 草稿.json --json                 # 给脚本读

退出码：0 通过 / 1 被拦 / 2 用法或配置问题。
"""
import argparse, datetime, hashlib, json, os, re, sys, unicodedata, zipfile

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

USER = os.path.join(DATA_DIR, 'user')
RECORD_DIR = os.path.join(USER, 'lint')


# ---------- 文本归一 ----------

def norm(s):
    """全角转半角、去掉所有空白。

    NFKC 会把「％」「０」这类全角字符折成半角，也会把「㈠」折成「(一)」，
    这样同一句话在不同输入法下写出来才能比较。
    """
    return ''.join(unicodedata.normalize('NFKC', str(s)).split())


# ---------- 三类事实的提取 ----------

# 日期要在数字之前匹配，否则「2026.07」会被数字规则拆成两段
DATE_RE = re.compile(r'(?:19|20)\d{2}(?:[.\-/年]\d{1,2}月?|年)')
NUM_RE = re.compile(r'\d+(?:\.\d+)?(?:%|万|千|亿)?\+?')
# 奖项：二等/三等/特等、金/银/铜；前面的层级词（省/国家…）单独看
AWARD_RE = re.compile(r'(国家|全国|省|市|校|院)?([一二三特]等奖|金|银|铜奖)')
LEVEL_WORD_RE = re.compile(r'(国家|全国|省|市|校|院)级')


def dates_in(text):
    return set(DATE_RE.findall(text))


def numbers_in(text, exclude_dates=True):
    """抽数字片段；exclude_dates=True 时把日期占的字符挖掉，避免重复报"""
    t = text
    if exclude_dates:
        t = DATE_RE.sub(lambda m: ' ' * len(m.group(0)), text)
    return set(NUM_RE.findall(t))


def awards_in(text):
    """返回 {(核心, 层级)}，层级可能是空串"""
    return {(m.group(2), m.group(1) or '') for m in AWARD_RE.finditer(text)}


def level_words_in(text):
    return set(LEVEL_WORD_RE.findall(text))


# ---------- 允许说法的来源 ----------

def docx_text(path):
    """从 docx 抽全部段落文字，保留原空格（与 fill_engine 同一套口径）"""
    try:
        x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8', 'ignore')
    except Exception:
        return ''
    out = []
    for p in re.findall(r'<w:p\b[^>]*>.*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p))
        if t.strip():
            out.append(t)
    return '\n'.join(out)


def load_sources():
    """把你的材料拼成一份「允许出现的事实语料」。

    三处：经历库、个人信息库、母版 Word。
    母版是基准——只改说法不改事实，所以母版里有的数字和等级都算有出处。
    """
    parts, got = [], {}

    exp = os.path.join(USER, 'experience.json')
    if os.path.exists(exp):
        parts.append(json.dumps(json.load(open(exp, encoding='utf-8-sig')),
                                ensure_ascii=False))
        got['experience'] = True
    else:
        got['experience'] = False

    prof = os.path.join(USER, 'profile.json')
    if os.path.exists(prof):
        parts.append(json.dumps(json.load(open(prof, encoding='utf-8-sig')),
                                ensure_ascii=False))
        got['profile'] = True
    else:
        got['profile'] = False

    master = (CFG.get('master_docx') or '').replace('/', os.sep)
    if master and os.path.exists(master):
        parts.append(docx_text(master))
        got['master_docx'] = master
    else:
        got['master_docx'] = ''

    return norm('\n'.join(parts)), got


# ---------- 禁用词 ----------

# 直接拦：无据的最高级说法，简历里出现就是硬伤
BANNED = [
    '唯一', '独家', '首家', '首创', '顶尖', '顶级', '领先行业', '业内领先', '行业领先',
    '业界最佳', '最强', '最优', '最大化', '极致', '完美', '零缺陷', '无死角',
    '颠覆', '革命性', '史无前例', '绝对领先', '无可比拟', '碾压', '第一品牌',
    '排名第一', '全国第一', '行业第一', '业内第一', '销量第一',
]

# 只提醒：形容词式自评——面试里被打过四次的那一类
SOFT = [
    '优秀', '出色', '卓越', '优异', '较强', '丰富', '显著提升', '大幅提升',
    '全面提升', '极大提升', '充分体现', '深入理解', '熟练掌握', '高效', '精准',
    '全面负责', '具有较强', '能力突出',
]


def find_words(text, words):
    return sorted({w for w in words if w in text})


# ---------- 主校验 ----------

def lint(edits, allow=()):
    corpus, sources = load_sources()
    allow_set = {norm(a) for a in allow if norm(a)}

    ok_nums = numbers_in(corpus)
    ok_dates = dates_in(corpus)
    ok_awards = awards_in(corpus)
    ok_level_words = level_words_in(corpus)
    award_levels = {}
    for core, lv in ok_awards:
        award_levels.setdefault(core, set()).add(lv)

    blocked, warnings, allowed_used = [], [], []

    def block(e, kind, token, detail):
        blocked.append(dict(kind=kind, token=token, old=e.get('old', '')[:40],
                            new=e.get('new', '')[:40], detail=detail))

    def warn(e, kind, token, detail):
        warnings.append(dict(kind=kind, token=token, old=e.get('old', '')[:40], detail=detail))

    for e in edits:
        old, new = str(e.get('old') or ''), str(e.get('new') or '')
        no, nn = norm(old), norm(new)

        # --- 事实关 1：新添的数字 ---
        for tok in sorted(numbers_in(nn) - numbers_in(no)):
            if tok in allow_set:
                allowed_used.append(tok)
                continue
            if tok in ok_nums:
                continue
            block(e, 'invented_number', tok,
                  f'「{tok}」在你的经历库、个人信息和母版简历里都找不到。'
                  f'要么删掉，要么回去把真实数字补进经历库再写。')

        # --- 事实关 2：新添的日期 ---
        for tok in sorted(dates_in(nn) - dates_in(no)):
            if tok in allow_set:
                allowed_used.append(tok)
                continue
            if tok in ok_dates:
                continue
            block(e, 'invented_date', tok,
                  f'「{tok}」不在你的材料里。时间只能引用，不能改写。')

        # --- 事实关 3：数字被换掉或删掉 ---
        gone = sorted(numbers_in(no) - numbers_in(nn))
        if gone:
            warn(e, 'dropped_number', '、'.join(gone),
                 f'原句里的「{"、".join(gone)}」在新句里没了。删数字通常等于改事实，'
                 f'确认一下是压缩措辞还是丢了成果。')

        # --- 事实关 4：奖项等级 ---
        for core, lv in sorted(awards_in(nn) - awards_in(no)):
            # 放行时用户可能写「国家二等奖」也可能写「二等奖国家」，两种都认；
            # 不认单独一个「二等奖」，否则等于把等级限制整个放开了
            if {norm(core) + norm(lv), norm(lv) + norm(core)} & allow_set:
                allowed_used.append(norm(lv) + norm(core))
                continue
            if core not in award_levels:
                block(e, 'invented_award', lv + core,
                      f'你的材料里没有任何「{core}」，这条像是新加的奖项。')
                continue
            if lv and lv not in award_levels[core]:
                have = '、'.join(f'{x}{core}' if x else core for x in sorted(award_levels[core]))
                block(e, 'award_level', lv + core,
                      f'你的材料里是「{have}」，这里写成了「{lv}{core}」。'
                      f'奖项等级是最容易被追问的一条，不能往上写。')

        for w in sorted(level_words_in(nn) - level_words_in(no)):
            if w in ok_level_words or w in allow_set:
                continue
            block(e, 'award_level', w,
                  f'你的材料里没有「{w}」这个层级说法，确认不是把省级写成了国家级。')

        # --- 说法关 ---
        for w in find_words(nn, BANNED):
            if w in no:
                continue          # 原文里就有，不追旧账
            if w in allow_set:
                allowed_used.append(w)
                continue
            block(e, 'banned_word', w,
                  f'「{w}」这类最高级说法会被面试官当场要证据，简历里不写。')

        for w in find_words(nn, SOFT):
            if w in no:
                continue
            warn(e, 'vague_claim', w,
                 f'「{w}」是形容词式自评。你的短板之一就是这个——换成做了什么、结果多少。')
            break                 # 一条改动只提醒一次，别刷屏

    # --- 全局：改动条数 ---
    # 按「段」算，不按「条」算：为了让格式不被破坏，一次改动常常拆成
    # 「小标题」「正文」两条分别替换，按条数会误判成改得太多。
    # 同一处改动如果为了保住格式拆成了多条，用同一个 grp 标记，只算一段
    paras = {str(e.get('grp') or ('单条%02d' % i)) for i, e in enumerate(edits)}
    if len(paras) > 5:
        warnings.append(dict(kind='too_many_edits', token=str(len(paras)),
                             old='', detail=f'这次改了 {len(paras)} 段，超过 5 段。'
                                           f'改动过多说明底稿选错了，回去重选一版再改。'))

    no_why = [str(i) for i, e in enumerate(edits, 1) if not str(e.get('why') or '').strip()]
    if no_why:
        warnings.append(dict(kind='no_reason', token=f'{len(no_why)} 条',
                             old='',
                             detail=f'第 {"、".join(no_why)} 条没写 why（为什么改）。'
                                    f'写给用户听的理由，也是自查。'))

    return dict(ok=not blocked, edits=len(edits), blocked=blocked,
                warnings=warnings, allowed=sorted(set(allowed_used)), sources=sources)


# ---------- 记录与输出 ----------

def safe_name(version):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', version).strip('_') or '未命名'


def write_record(version, edits, result):
    os.makedirs(RECORD_DIR, exist_ok=True)
    blob = json.dumps(edits, ensure_ascii=False, sort_keys=True)
    rec = dict(version=version, ok=result['ok'],
               at=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
               draft_sha1=hashlib.sha1(blob.encode('utf-8')).hexdigest()[:12],
               edits=result['edits'], blocked=len(result['blocked']),
               warnings=len(result['warnings']))
    path = os.path.join(RECORD_DIR, safe_name(version) + '.json')
    json.dump(rec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return path


def print_text(result):
    src = result['sources']
    used = [k for k in ('experience', 'profile') if src.get(k)] + \
           (['母版 Word'] if src.get('master_docx') else [])
    print(f'=== 改动校验（对照：{"、".join(used) or "无"}）===\n')
    if not src.get('master_docx'):
        print('  ! 没配母版 Word，只拿信息库对照，可能有误报。\n')

    if result['blocked']:
        print(f'拦住 {len(result["blocked"])} 条：')
        for b in result['blocked']:
            print(f'  ✗ [{b["kind"]}] {b["detail"]}')
            print(f'     原句：{b["old"]}…')
        print()
    if result['warnings']:
        print(f'提醒 {len(result["warnings"])} 条（不拦，但要让用户看见）：')
        for w in result['warnings']:
            print(f'  · [{w["kind"]}] {w["detail"]}')
        print()
    if result['allowed']:
        print('用户明确放行：' + '、'.join(result['allowed']) + '\n')

    if result['ok']:
        print(f'结论：{result["edits"]} 条改动通过事实关与说法关 ✓')
    else:
        print('结论：不通过。改草稿重跑，最多两次；两次都过不了就换回原版，'
              '并告诉用户「这条我改不好」。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('draft')
    ap.add_argument('--allow', action='append', default=[],
                    help='明确放行某个词，可重复；会在输出里列出来')
    ap.add_argument('--record', default='', help='通过后按这个版本名落记录')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if not os.path.exists(a.draft):
        print(f'找不到草稿：{a.draft}')
        return 2
    try:
        edits = json.load(open(a.draft, encoding='utf-8-sig'))
    except Exception as ex:
        print(f'草稿不是合法 JSON：{ex}')
        return 2
    if isinstance(edits, dict):
        edits = edits.get('edits') or []
    if not isinstance(edits, list) or not edits:
        print('草稿是空的，没有可校验的改动。')
        return 2

    result = lint(edits, a.allow)
    if a.record:
        result['record'] = write_record(a.record, edits, result)

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print_text(result)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
