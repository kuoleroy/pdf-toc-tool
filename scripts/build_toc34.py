# -*- coding: utf-8 -*-
"""第3/4卷书签构建：解析 标题…点线…页码(范围) 格式的txt目录
结构规则：
- 缩进0的行 = 独立著作候选；若其起始页落在前面某著作的[起始,结束)区间内 -> 降为L2
- 其余按缩进：1级缩进 -> L2，>=2级缩进 -> L3
- 无页码的行视为上一标题的续行（合并到有页码的行）
- 特例：当前L2为 增补 时，其子项(一~五)为L3
"""
import fitz, shutil, os, re, sys

VOL = int(sys.argv[1]) if len(sys.argv) > 1 else 3
BASE = r'C:\Users\Administrator\Desktop\social-theory\列宁选集'
PDF = os.path.join(BASE, '列宁选集第%d卷72版.pdf' % VOL)
TXT = os.path.join(BASE, '列宁选集第%d卷72版-目录.txt' % VOL)
OUT = os.path.join(BASE, '列宁选集第%d卷72版_带目录.pdf' % VOL)
OFFSET = 15 if VOL != 4 else 13  # 0-based pdf index = 印刷页码 + OFFSET

# ---- 标题修正：页码 -> (匹配片段, 完整标题) ----
TITLES = {
    345: [('丁武装起义', '俄国社会民主工党中央委员会关于武装起义的决议')],
    168: [('革命民主流', '革命民主派和革命无产阶级')],
    475: [('（1918年3月）8日', '关于修改党纲和更改党的名称的报告（1918年3月8日晚上在俄共（布）第七次代表大会上）')],
}

# ---- 解析txt ----
entries = []  # (indent, title, start, end)
pending = None  # [indent, 续行累计]
re_range = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*[—–\-]\s*(\d+)\s*$')
re_single = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*$')
with open(TXT, encoding='utf-8-sig') as f:
    for line in f:
        line = line.rstrip('\r\n')
        if not line.strip():
            continue
        if re.match(r'^\s*目\s*录\s*$', line):
            continue
        if re.match(r'^\s*\d{4}\s*[—–\-]\s*\d{4}\s*年?\s*$', line):
            continue  # 年代行 如 1917—1919年
        m = re_range.match(line) or re_single.match(line)
        if not m:
            ind = len(re.match(r'^(　*)', line).group(1))
            if pending is None:
                pending = [ind, line.strip()]
            else:
                pending[1] += line.strip()
            continue
        indent_s, title = m.group(1), m.group(2)
        start = int(m.group(3))
        end = int(m.group(4)) if m.lastindex >= 4 else start
        if pending is not None:
            title = pending[1] + title
            indent = pending[0]
            pending = None
        else:
            indent = len(indent_s)
        title = re.sub(r'[…\.]+$', '', title).strip()
        if start in TITLES:
            for frag, repl in TITLES[start]:
                if frag in title:
                    title = repl
                    break
        entries.append([indent, title, start, end])

if pending is not None:
    print('WARN: dangling continuation:', repr(pending))

# ---- 分配层级 ----
leveled = []  # (level, title, start)
works = []    # 已接受的L1著作 [(start, end)]
cur_l2 = None  # 当前L2标题
cur_l1 = None  # 当前L1著作标题
special_l1 = '全俄工兵代表苏维埃第二次代表大会（1917年10月25—26日〔11月7—8日〕）（摘录）'
for indent, title, start, end in entries:
    if indent == 0:
        inside = any(w[0] <= start < w[1] for w in works)
        if not inside:
            works.append((start, end))
            level = 1
            cur_l1 = title
            cur_l2 = None
        else:
            level = 2
            cur_l2 = title
    else:
        if indent >= 2 or cur_l2 == '增补':
            level = 3
        else:
            level = 2
    # 全俄苏维埃二大 特例：数字编号子节(1-4) -> L2，其余(法令) -> L3
    if level == 3 and cur_l1 == special_l1 and re.match(r'^\d+[ 　]', title):
        level = 2
    leveled.append([level, title, start])

# ---- 生成书签 ----
fitz_toc = []
for lvl, title, start in leveled:
    pdf_page = start + OFFSET + 1
    if not (1 <= pdf_page <= 2000):
        print('WARN out of range:', lvl, title, start)
    fitz_toc.append([lvl, title, pdf_page])

print('total entries:', len(fitz_toc))
for lvl, title, page in fitz_toc:
    print('%d | %s | p%d' % (lvl, title, page - OFFSET - 1))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'v%d_result.txt' % VOL),
          'w', encoding='utf-8') as f:
    for lvl, title, page in fitz_toc:
        f.write('%d | %s | p%d\n' % (lvl, title, page - OFFSET - 1))

shutil.copyfile(PDF, OUT)
doc = fitz.open(OUT)
doc.set_toc(fitz_toc)
doc.save(OUT, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
doc.close()

d2 = fitz.open(OUT)
print('bookmarks written:', len(d2.get_toc()))
d2.close()
print('out size:', os.path.getsize(OUT))
