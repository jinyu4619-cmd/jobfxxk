# -*- coding: utf-8 -*-
"""docx_fill.py —— 在 XML 字符串层替换，保住格式；顺手把成果数据加粗

为什么不用 Word 的查找替换：Word 的 Find.Execute 会把匹配到的整段合并成一个 run，
新文字继承匹配起点那个 run 的格式。小标题是粗体、正文是常规体，一次替换跨过这条
边界，整段就变成粗体——真实踩过。

为什么不在 ElementTree 里重建整棵树：命名空间前缀会被改写、嵌套段落容易丢，
实测把文档从 60 段变成 52 段。改成只对命中的那一段做字符串级手术，其余一个字节不动。

替换完再交给 Word，只做一件事：导出 PDF。
"""
import re
import zipfile

# 成果数据：百分数、比率、倍率。45 人 / 10+ 位 / 7 次 这类计数不加粗。
RESULT_RE = re.compile(r'\d+(?:\.\d+)?\s*[%％]|\d+(?:\.\d+)?\s*/\s*\d+|\d+(?:\.\d+)?\s*倍')

PARA_RE = re.compile(r'<w:p\b[^>]*>.*?</w:p>', re.S)
RUN_RE = re.compile(r'<w:r\b[^>]*>.*?</w:r>', re.S)
T_RE = re.compile(r'<w:t[^>]*>(.*?)</w:t>', re.S)


def nospace(s):
    return ''.join((s or '').split())


def unescape(s):
    return (s.replace('&lt;', '<').replace('&gt;', '>')
             .replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&'))


def escape(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def run_is_bold(run_xml):
    head = run_xml[:run_xml.find('>') + 1]
    if 'rPr' not in run_xml:
        return False
    m = re.search(r'<w:rPr>.*?</w:rPr>', run_xml, re.S)
    if not m:
        return False
    rpr = m.group(0)
    for b in re.findall(r'<w:b\b[^>]*/?>', rpr):
        if 'w:val="0"' in b or 'w:val="false"' in b:
            return False
        return True
    return False


def make_run(template_run, text, force_bold=None):
    """以 template_run 的 rPr 为模板，造一个只含 text 的 run"""
    rpr = re.search(r'<w:rPr>.*?</w:rPr>', template_run, re.S)
    rpr = rpr.group(0) if rpr else ''
    if force_bold is True and '<w:b' not in rpr:
        rpr = rpr.replace('</w:rPr>', '<w:b/></w:rPr>') if rpr else '<w:rPr><w:b/></w:rPr>'
    elif force_bold is False:
        rpr = re.sub(r'<w:b\b[^>]*/>', '', rpr)
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(text))


def split_by_result(text):
    out, pos = [], 0
    for m in RESULT_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [(text, False)]



def diff_edges(old, new):
    """新旧文本的公共前缀长度、公共后缀长度（按字符算）"""
    a, b = old or '', new or ''
    n = min(len(a), len(b))
    pre = 0
    while pre < n and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < n - pre and a[len(a) - 1 - suf] == b[len(b) - 1 - suf]:
        suf += 1
    return pre, suf




def bold_results_only(para_xml):
    """只把成果数据（百分数/比率/倍率）加粗，其余一个字节不动"""
    def fix(m):
        run = m.group(0)
        text = unescape(''.join(T_RE.findall(run)))
        if not text or not RESULT_RE.search(text) or run_is_bold(run):
            return run
        segs = split_by_result(text)
        if not any(is_res for _, is_res in segs):
            return run
        return ''.join(make_run(run, seg, force_bold=True if is_res else None)
                       for seg, is_res in segs if seg)
    return RUN_RE.sub(fix, para_xml)


def rebuild_para_format(para_xml):
    """整段重建加粗：小标题（到第一个「：」为止）加粗、成果数据加粗、其余常规

    为什么整段重建而不是只动改过的地方：一条替换如果跨了「粗体小标题」和「常规正文」，
    拆 run 很难对齐；直接从整段文字重新生成 run 更稳，结果也确定。
    """
    runs = RUN_RE.findall(para_xml)
    if not runs:
        return para_xml
    text = unescape(''.join(''.join(T_RE.findall(r)) for r in runs))
    if not text.strip():
        return para_xml

    template = runs[0]
    # 小标题：开头到第一个「：」（含），且长度不超过 24 字，否则认为整段没有小标题
    cut = text.find('：')
    title_len = cut + 1 if 0 < cut <= 24 else 0

    segs = []
    if title_len:
        segs.append((text[:title_len], True))
    rest = text[title_len:]
    for seg, is_res in split_by_result(rest):
        segs.append((seg, is_res))

    new_runs = ''.join(make_run(template, seg, force_bold=True if bold else False)
                       for seg, bold in segs if seg)

    # 只替换 run 部分，段落属性（pPr）原样保留。
    # 注意不能用 find('<w:r')——那样会匹到 <w:rPr>，切在段落属性中间，XML 直接坏掉。
    ms = list(RUN_RE.finditer(para_xml))
    if not ms:
        return para_xml
    return para_xml[:ms[0].start()] + new_runs + para_xml[ms[-1].end():]


def replace_in_document(docx_path, out_path, pairs):
    """pairs: [(old, new)]。返回 (成功条数, 失败列表[(原因, 片段)])"""
    zin = zipfile.ZipFile(docx_path)
    xml = zin.read('word/document.xml').decode('utf-8')

    done, failed = 0, []

    for old, new in pairs:
        target = nospace(old)
        if not target:
            failed.append(('空片段（新旧文本没有差异）', (new or '')[:30]))
            continue

        replaced_here = 0
        out_parts, pos = [], 0

        for m in PARA_RE.finditer(xml):
            para = m.group(0)
            runs = [(rm.start() + m.start(), rm.end() + m.start(), rm.group(0))
                    for rm in RUN_RE.finditer(para)]
            texts = []
            for _, _, rx in runs:
                texts.append(unescape(''.join(T_RE.findall(rx))))
            full = ''.join(texts)
            flat = nospace(full)
            k = flat.find(target)
            if k < 0:
                continue

            # 去空格位置 → 原文位置
            idx = [i for i, ch in enumerate(full) if not ch.isspace()]

            # 只在定位到的 old 内部做差分替换。
            # 直接把差分片段拿去全文搜索会改错地方——真实踩过：
            # 「策划 2 次节日推荐活动」差分成「推荐」，结果改到了更靠前的「推荐位选品规则」。
            pre, suf = diff_edges(old, new)
            t_new = (new or '')[pre:len(new) - suf] if suf else (new or '')[pre:]
            p_start = k + len(nospace((old or '')[:pre]))
            p_end = k + len(target) - (len(nospace((old or '')[len(old) - suf:])) if suf else 0)
            if p_end <= p_start:
                continue
            start, end = idx[p_start], idx[p_end - 1] + 1

            # 覆盖到的 run
            spans, p2 = [], 0
            for i, t in enumerate(texts):
                spans.append((p2, p2 + len(t), i))
                p2 += len(t)
            cover = [(a, b, i) for (a, b, i) in spans if a < end and b > start]
            if not cover:
                continue

            # 跨粗体边界不再拒绝：替换后整段重建格式（标题加粗、成果数据加粗、其余常规）
            base_bold = run_is_bold(runs[cover[0][2]][2])
            first_a, _, first_i = cover[0]
            last_b, _, last_i = cover[-1]
            head = texts[first_i][:start - first_a]
            tail = texts[last_i][end - last_b:]
            template = runs[first_i][2]

            new_runs = []
            if head:
                new_runs.append(make_run(template, head, force_bold=base_bold))
            for seg, is_res in split_by_result(t_new):
                new_runs.append(make_run(template, seg,
                                         force_bold=True if is_res else base_bold))
            if tail:
                new_runs.append(make_run(template, tail, force_bold=base_bold))

            # 只替换这一段里被覆盖的 run，段内其它内容原样保留
            para_pos = 0
            pieces = []
            for i, (rs, re_, rx) in enumerate(runs):
                local_rs = rs - m.start()
                local_re = re_ - m.start()
                if i == first_i:
                    pieces.append(para[para_pos:local_rs])
                    pieces.append(''.join(new_runs))
                    para_pos = local_re
                elif first_i < i <= last_i:
                    para_pos = local_re
            pieces.append(para[para_pos:])
            new_para = rebuild_para_format(''.join(pieces))

            out_parts.append(xml[pos:m.start()])
            out_parts.append(new_para)
            pos = m.end()
            replaced_here += 1
            # 不 break：正文和文本框常存两份副本，只改一处会导致两处不一致

        if replaced_here:
            out_parts.append(xml[pos:])
            xml = ''.join(out_parts)
            done += 1
        elif not any(f[1] == old[:30] for f in failed):
            failed.append(('文档里没找到', old[:30]))

    # 整篇统一：只给成果数据补加粗，不重建整段——
    # 重建会毁掉未改段落里本来就有的加粗（姓名、日期、公司名）。
    xml = PARA_RE.sub(lambda m: bold_results_only(m.group(0)), xml)

    zout = zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED)
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == 'word/document.xml':
            data = xml.encode('utf-8')
        zout.writestr(item, data)
    zout.close()
    zin.close()
    return done, failed
