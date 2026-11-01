# -*- coding: utf-8 -*-
"""把简历读成干净的文本，供后面结构化成经历

用法:
  python scripts/read_resume.py <文件>          # 人看
  python scripts/read_resume.py <文件> --json   # 给 agent 读

支持 docx / pdf / txt / md。三种文件各有各的坑，这里都处理了：
  docx —— 文字常在文本框里；AlternateContent 让同一段出现两遍；要按原文顺序取
  pdf  —— 抽出来的字之间常夹空格（1 2 0 0 +），先去空格再还原
  图片 —— 读不了，直接告诉用户改用 docx 或口述，不猜
"""
import io, json, os, re, sys, zipfile
sys.stdout.reconfigure(encoding='utf-8')


def from_docx(path):
    x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8', 'ignore')
    x = re.sub(r'<w:proofErr[^>]*/>', '', x)
    out, seen = [], set()
    # 按 </w:p> 切开，保证文本框里的段落也能被单独取出
    for chunk in x.split('</w:p>'):
        texts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', chunk)
        t = ''.join(texts)
        if not t.strip():
            continue
        key = re.sub(r'\s', '', t)
        if key in seen:            # AlternateContent 存了两份副本，去重
            continue
        seen.add(key)
        out.append({'text': t, 'in_textbox': '<w:txbxContent>' in chunk})
    return out


def from_pdf(path):
    from pypdf import PdfReader
    rd = PdfReader(path)
    paras = []
    for pg in rd.pages:
        raw = pg.extract_text() or ''
        for line in raw.splitlines():
            t = line.strip()
            if not t:
                continue
            # PDF 常用「每个字之间加空格」的方式排版，中文之间先去掉空格
            if re.search(r'(?<=[一-鿿])\s+(?=[一-鿿])', t):
                t = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', t)
            t = re.sub(r'[ \t]{2,}', ' ', t).strip()
            if t:
                paras.append({'text': t, 'in_textbox': False})
    return paras


def from_text(path):
    s = io.open(path, encoding='utf-8-sig', errors='replace').read()
    return [{'text': l.strip(), 'in_textbox': False}
            for l in s.splitlines() if l.strip()]


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    path = argv[0]
    as_json = '--json' in argv

    if not os.path.exists(path):
        sys.exit(f'找不到文件：{path}')
    ext = os.path.splitext(path)[1].lower()

    try:
        if ext == '.docx':
            paras, kind = from_docx(path), 'docx'
        elif ext == '.pdf':
            paras, kind = from_pdf(path), 'pdf'
        elif ext in ('.txt', '.md'):
            paras, kind = from_text(path), ext[1:]
        elif ext in ('.doc',):
            sys.exit('这是老式 .doc，读不了。用 Word 另存为 .docx 再给我。')
        elif ext in ('.png', '.jpg', '.jpeg', '.webp'):
            sys.exit('图片我不直接读，容易把数字认错。把图里的内容用文字发我，或者给我 docx/pdf。')
        else:
            sys.exit(f'不认识的文件类型：{ext}。给我 docx、pdf 或直接口述。')
    except Exception as e:
        sys.exit(f'读这个文件时出错了：{str(e)[:120]}')

    if not paras:
        sys.exit('文件打开了，但一个字都没读到。可能是扫描件或图片转的 PDF。')

    text = '\n'.join(p['text'] for p in paras)
    payload = {
        'ok': True,
        'kind': kind,
        'chars': len(re.sub(r'\s', '', text)),
        'paragraphs': len(paras),
        'textbox_paragraphs': sum(1 for p in paras if p['in_textbox']),
        'numbers': re.findall(r'\d+(?:\.\d+)?%?', re.sub(r'\s', '', text))[:40],
        'lines': [p['text'] for p in paras],
    }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return

    print(f'文件：{path}')
    print(f'类型：{kind} ｜ 段落：{len(paras)} ｜ 字数：{payload["chars"]} ｜ '
          f'其中文本框段落：{payload["textbox_paragraphs"]}')
    print(f'抽到的数字 {len(payload["numbers"])} 个：' + '、'.join(payload['numbers'][:20]))
    print('-' * 50)
    for i, p in enumerate(paras, 1):
        mark = '[框]' if p['in_textbox'] else '    '
        print(f'{i:>3} {mark} {p["text"]}')


if __name__ == '__main__':
    main()
