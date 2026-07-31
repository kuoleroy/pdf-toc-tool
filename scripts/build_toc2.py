# -*- coding: utf-8 -*-
"""解析用户提供的第2卷目录txt并生成书签 v3
以书籍真实结构为准规整层级，txt只提供(标题,页码)数据"""
import fitz, shutil, os, re

PDF = r'C:\Users\Administrator\Desktop\social-theory\列宁选集\列宁选集第2卷72版.pdf'
TXT = r'C:\Users\Administrator\Desktop\social-theory\列宁选集\列宁选集第2卷72版-目录.txt'
OUT = r'C:\Users\Administrator\Desktop\social-theory\列宁选集\列宁选集第2卷72版_带目录.pdf'

def P(p):
    return p + 16

# ---- 解析txt ----
raw = []
with open(TXT, encoding='utf-8-sig') as f:
    for line in f:
        line = line.rstrip('\n').rstrip('\r')
        if not line.strip():
            continue
        m = re.match(r'^(\s*)(.*?)\s*(\d+)\s*$', line)
        if not m:
            print('SKIP:', repr(line))
            continue
        indent, title, page = m.group(1), m.group(2), int(m.group(3))
        raw.append([title, page])

# ---- 标题修正（错字+合并拆行）：每页多组(匹配片段, 完整标题) ----
TITLES = {
    16: [('代绪论', '代绪论 某些“马克思主义者”在1908年和某些唯心主义者在1910年是怎样驳斥唯物主义的')],
    34: [('第一章', '第一章 经验批判主义的认识论和辩证唯物主义的认识论（一）'),
         ('认识论（一）', '第一章 经验批判主义的认识论和辩证唯物主义的认识论（一）')],
    95: [('第二章', '第二章 经验批判主义的认识论和辩证唯物主义的认识论（二）'),
         ('认识论（二）', '第二章 经验批判主义的认识论和辩证唯物主义的认识论（二）')],
    144: [('第三章', '第三章 辩证唯物主义的认识论和经验批判主义的认识论（三）'),
          ('认识论（三）', '第三章 辩证唯物主义的认识论和经验批判主义的认识论（三）')],
    196: [('第四章', '第四章 作为经验批判主义的战友和继承者的哲学唯心主义者'),
          ('唯心主义者', '第四章 作为经验批判主义的战友和继承者的哲学唯心主义者')],
    130: [('五 绝对真理', '五 绝对真理和相对真理，或论波格丹诺夫所发现的恩格斯的折衷主义'),
          ('恩格斯的折衷主义', '五 绝对真理和相对真理，或论波格丹诺夫所发现的恩格斯的折衷主义')],
    207: [('切尔诺夫', '二 “经验符号论者”尤什凯维奇怎样嘲笑“经验批判主义者”切尔诺夫')],
    221: [('验批判主义', '四 经验批判主义往哪里发展？')],
    342: [('学上的党派', '四 哲学上的党派和哲学上的无头脑者')],
    398: [('论马克思主义历史发展中的几个特点', '论马克思主义历史发展中的几个特点')],
    679: [('同机会主义者统一', '同机会主义者统一就是工人同“本”民族的资产阶级结成联盟，分裂国际革命工人阶级')],
    730: [('帝国主义是资本主义的最高阶段', '帝国主义是资本主义的最高阶段（通俗的论述）')],
    796: [('强分割世界', '六 帝国主义列强分割世界')],
    817: [('本主义的寄生性', '八 帝国主义的寄生性和腐朽')],
    840: [('国主义的历史地位', '十 帝国主义的历史地位')],
}
DROP_PAGES = {680}  # 679的续行
raw = [e for e in raw if e[1] not in DROP_PAGES]
for e in raw:
    if e[1] in TITLES:
        for frag, repl in TITLES[e[1]]:
            if frag in e[0]:
                e[0] = repl
                break

# ---- 结构定义：独立著作(顶层)列表 ----
STANDALONE = [
    '马克思主义和修正主义', '向报告人提十个问题',
    '唯物主义和经验批判主义（对一种反动哲学的批判）',
    '列甫·托尔斯泰是俄国革命的镜子', '论工人政党对宗教的态度', '革命的教训',
    '欧洲工人运动中的分歧', '论马克思主义历史发展中的几个特点',
    '俄国社会民主主义运动中的改良主义', '纪念赫尔岑', '中国的民主主义和民粹主义',
    '两种乌托邦', '欧仁·鲍狄埃（为纪念他逝世二十五周年而作）', '马克思学说的历史命运',
    '马克思主义的三个来源和三个组成部分', '亚洲的觉醒', '落后的欧洲和先进的亚洲',
    '论自由主义和马克思主义的阶级斗争概念 短评', '几个争论问题（公开党和马克思主义者）',
    '给阿·马·高尔基', '论高喊统一而实则破坏统一的行为', '论民族自决权',
    '战争和俄国社会民主党', '卡尔·马克思（传略和马克思主义概述）（摘录）',
    '辩证法的要素（摘自《黑格尔〈逻辑学〉一书摘要》）', '论大俄罗斯人的民族自豪感',
    '第二国际的破产', '社会主义与战争（俄国社会民主工党对战争的态度）',
    '论欧洲联邦口号', '谈谈辩证法问题', '社会主义革命和民族自决权 提纲',
    '帝国主义是资本主义的最高阶段（通俗的论述）', '论尤尼乌斯的小册子',
    '关于自决问题的争论总结（摘录）', '无产阶级革命的军事纲领',
    '帝国主义和社会主义运动中的分裂', '资产阶级的和平主义与社会党人的和平主义',
    '注释', '人名索引',
]
STANDALONE = set(STANDALONE)

# 唯物主义和经验批判主义 内部：章级条目 -> L2
MAT_L2 = {
    '第一版序言', '第二版序言',
    '代绪论 某些“马克思主义者”在1908年和某些唯心主义者在1910年是怎样驳斥唯物主义的',
    '第一章 经验批判主义的认识论和辩证唯物主义的认识论（一）',
    '第二章 经验批判主义的认识论和辩证唯物主义的认识论（二）',
    '第三章 辩证唯物主义的认识论和经验批判主义的认识论（三）',
    '第四章 作为经验批判主义的战友和继承者的哲学唯心主义者',
    '第五章 最近的自然科学革命和哲学唯心主义',
    '第六章 经验批判主义和历史唯物主义',
    '结论', '第四章第一节的补充 车尔尼雪夫斯基是从哪一边批判康德主义的？',
}
# 卡尔·马克思 内部：L2
KM_L2 = {'序言', '马克思的学说', '马克思的经济学说', '社会主义', '无产阶级阶级斗争的策略'}
# 社会主义与战争 内部：L2
SW_L2 = {'初版（国外版）序言', '第二版序言', '第一章 社会主义的原则和1914—1915年的战争',
         '第二章 俄国的阶级和政党', '第三章 恢复国际', '第四章 俄国社会民主党的分裂史和现状'}
# 帝国主义 内部：L2
IMP_L2 = {'序言', '法文版和德文版序言'}
IMP_CH = {'一 生产集中和垄断', '二 银行和银行的新作用', '三 金融资本和金融寡头', '四 资本输出',
          '五 资本家同盟分割世界', '六 帝国主义列强分割世界', '七 帝国主义是资本主义的特殊阶段',
          '八 帝国主义的寄生性和腐朽', '九 对帝国主义的批评', '十 帝国主义的历史地位'}

# ---- 分配层级 ----
out = []  # (level, title, page)
cur_block = None  # 当前所属顶层著作

def final_level(title, page):
    if cur_block is None:
        return 1
    b = cur_block
    if b == '唯物主义和经验批判主义（对一种反动哲学的批判）':
        return 2 if title in MAT_L2 else 3
    if b == '卡尔·马克思（传略和马克思主义概述）（摘录）':
        return 2 if title in KM_L2 else 3
    if b == '社会主义与战争（俄国社会民主工党对战争的态度）':
        # 两篇序言 + 四个章标题 -> L2，其余子节 -> L3
        return 2 if title in SW_L2 else 3
    if b == '帝国主义是资本主义的最高阶段（通俗的论述）':
        if title in IMP_L2 or title in IMP_CH:
            return 2
        return 3
    return 2

for title, page in raw:
    is_standalone = title in STANDALONE
    # 社会主义与战争 区间(666-705)内的同名标题不算顶层
    if is_standalone and 666 <= page <= 705 and title not in ('社会主义与战争（俄国社会民主工党对战争的态度）',):
        is_standalone = False
    if is_standalone:
        cur_block = title
        out.append([1, title, page])
    else:
        lvl = final_level(title, page)
        out.append([lvl, title, page])

# 去重：同一(页,标题)只保留一条（保留L2层级的那条）
seen = {}
dedup = []
for lvl, title, page in out:
    key = (title, page)
    if key not in seen:
        seen[key] = lvl
        dedup.append([lvl, title, page])
out = dedup

# 补充分页：马克思学说的历史命运 的"一"（437=著作起始页）
# 找到该著作插入"一"节
final = []
for lvl, title, page in out:
    final.append([lvl, title, page])
    if title == '马克思学说的历史命运' and page == 437:
        final.append([2, '一', 437])

# ---- 生成书签 ----
fitz_toc = []
for lvl, title, page in final:
    pdf_page = P(page)
    if not (1 <= pdf_page <= 1024):
        print('WARN out of range:', lvl, title, page)
    fitz_toc.append([lvl, title, pdf_page])

print('total entries:', len(fitz_toc))
for lvl, title, page in fitz_toc:
    print('%d | %s | p%d' % (lvl, title, page - 16))

# 写日志文件供检查
with open(r'C:\Users\ADMINI~1\AppData\Local\Temp\opencode\v2_result.txt', 'w', encoding='utf-8') as f:
    for lvl, title, page in fitz_toc:
        f.write('%d | %s | p%d\n' % (lvl, title, page - 16))

shutil.copyfile(PDF, OUT)
doc = fitz.open(OUT)
doc.set_toc(fitz_toc)
doc.save(OUT, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
doc.close()

d2 = fitz.open(OUT)
print('bookmarks written:', len(d2.get_toc()))
d2.close()
print('out size:', os.path.getsize(OUT))
