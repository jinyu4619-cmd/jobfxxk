# -*- coding: utf-8 -*-
"""docx 的 XML 层整段替换，覆盖文本框里的重复副本。

母版里每段在 AlternateContent 下存了两份（Word 一份、兜底一份），
只在段落层改一次会漏掉另一份，所以这里按 w:p 遍历、命中即整段重写。
"""
import json
import re
import shutil
import sys
import zipfile

sys.path.insert(0, r'D:\27秋招\jobfxxk\scripts')
import docx_fill as df


def convert_para(para_xml, new_text):
    runs = df.RUN_RE.findall(para_xml)
    if not runs:
        return None
    ppr = re.search(r'<w:pPr>.*?</w:pPr>', para_xml, re.S)
    head = para_xml[:para_xml.index('>') + 1]
    return head + (ppr.group(0) if ppr else '') + df.make_run(runs[0], new_text) + '</w:p>'


def apply(path, norm):
    """norm: {去空格后的整段原文: 新文本}。返回 ({key: 命中份数}, {未命中的 key: 0})"""
    hits = {k: 0 for k in norm}
    tmp = path + '.tmp'
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith('.xml'):
                xml = data.decode('utf-8', 'ignore')
                if '<w:p' in xml:
                    for para in df.PARA_RE.findall(xml):
                        text = ''.join(df.unescape(t) for t in df.T_RE.findall(para))
                        key = text.replace(' ', '')
                        if key in norm:
                            newp = convert_para(para, norm[key])
                            if newp:
                                xml = xml.replace(para, newp)
                                hits[key] += 1
                    data = xml.encode('utf-8')
            zout.writestr(item, data)
    shutil.move(tmp, path)
    done = {k: v for k, v in hits.items() if v}
    miss = {k: v for k, v in hits.items() if not v}
    return done, miss


def main():
    path, draft = sys.argv[1], sys.argv[2]
    edits = json.load(open(draft, encoding='utf-8'))
    norm = {e['old'].replace(' ', ''): e['new'] for e in edits}
    shutil.copy(path, path + '.bak')
    done, miss = apply(path, norm)
    for k, v in done.items():
        print('✓', v, '份 <-', k[:26])
    for k in miss:
        print('✗ 没找到 <-', k[:26])


if __name__ == '__main__':
    main()
