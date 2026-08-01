# -*- coding: utf-8 -*-
"""电子书内置目录提取：EPUB / MOBI / AZW3 / PRC

输出与 PDF 书签 txt 兼容：行首缩进定层级，行尾 [p序号]。
电子书没有页码，[p序号] 为该目录项在书中出现的阅读顺序号（1 起），
可用于查看/整理目录；如需写入 PDF，请将序号替换为真实 PDF 页号。

实现仅用标准库（zipfile + xml），不引入额外依赖。
"""
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET

BOOK_EXTS = {'.epub', '.mobi', '.azw3', '.prc', '.azw'}

_NCX_NS = 'http://www.daisy.org/z3986/2005/ncx/'
_XHTML_NS = 'http://www.w3.org/1999/xhtml'
_OPF_NS = 'http://www.idpf.org/2007/opf'
_OPS_NS = 'http://www.idpf.org/2007/ops'


def is_ebook(path):
    return os.path.splitext(path)[1].lower() in BOOK_EXTS


def extract_toc(path):
    """提取电子书内置目录 -> [(indent, title, seq)]，indent>=0，seq>=1"""
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        raise ValueError('文件不存在: %s' % path)
    if ext == '.epub':
        return _epub_toc(path)
    if ext in ('.mobi', '.azw3', '.prc', '.azw'):
        return _parse_mobi_toc(path)
    raise ValueError('不支持的电子书格式: %s（支持 %s）' % (ext, ' / '.join(sorted(BOOK_EXTS))))


def to_txt(entries):
    """目录条目 -> txt 文本（缩进 + 标题 + [p序号]）"""
    lines = []
    for indent, title, seq in entries:
        lines.append('  ' * indent + title + ' [p%d]' % seq)
    return '\n'.join(lines) + '\n'


def _local(tag):
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _clean(title):
    title = re.sub(r'\s+', ' ', title or '').strip(' \t\r\n\ufeff')
    return title


# ---------------------------------------------------------------- EPUB
def _epub_toc(path):
    with zipfile.ZipFile(path) as z:
        try:
            container = z.read('META-INF/container.xml')
        except KeyError:
            raise ValueError('不是有效的EPUB文件（缺少 META-INF/container.xml）')
        root = ET.fromstring(container)
        rootfile = root.find('.//{%s}rootfile' % 'urn:oasis:names:tc:opendocument:xmlns:container')
        if rootfile is None:
            raise ValueError('EPUB容器中未找到 rootfile')
        opf_path = rootfile.get('full-path') or ''
        opf_dir = os.path.dirname(opf_path).replace('\\', '/')
        opf = ET.fromstring(z.read(opf_path))

        nav_href = None
        ncx_id = None
        items = {}
        for item in opf.findall('.//{%s}item' % _OPF_NS):
            item_id = item.get('id')
            href = item.get('href')
            items[item_id] = href
            props = (item.get('properties') or '').split()
            if 'nav' in props:
                nav_href = href
        spine = opf.find('.//{%s}spine' % _OPF_NS)
        if spine is not None:
            ncx_id = spine.get('toc')
        ncx_href = items.get(ncx_id) if ncx_id else None
        for item in opf.findall('.//{%s}item' % _OPF_NS):
            if item.get('media-type') == 'application/x-dtbncx+xml':
                ncx_href = item.get('href')

        href = nav_href or ncx_href
        if not href:
            raise ValueError('EPUB中未找到目录文件（nav/ncx）')
        full = opf_dir + '/' + href if opf_dir else href
        data = z.read(full.lstrip('/'))
        if nav_href:
            return _parse_nav_html(data)
        return _parse_ncx(data)


def _parse_nav_html(data):
    root = ET.fromstring(data)
    toc_nav = None
    for nav in root.iter('{%s}nav' % _XHTML_NS):
        if nav.get('{%s}type' % _OPS_NS) == 'toc':
            toc_nav = nav
            break
    if toc_nav is None:
        navs = list(root.iter('{%s}nav' % _XHTML_NS))
        if navs:
            toc_nav = navs[0]
    if toc_nav is None:
        raise ValueError('未找到目录（nav）')
    seq = [0]
    out = []

    def walk(ol, depth):
        for li in ol.findall('{%s}li' % _XHTML_NS):
            a = li.find('{%s}a' % _XHTML_NS)
            if a is not None:
                seq[0] += 1
                title = _clean(''.join(a.itertext()))
                if title:
                    out.append((depth, title, seq[0]))
            for sub in li:
                if _local(sub.tag) == 'ol':
                    walk(sub, depth + 1)

    for ol in toc_nav.findall('{%s}ol' % _XHTML_NS):
        walk(ol, 0)
    if not out:
        raise ValueError('目录（nav）为空')
    return out


# ---------------------------------------------------------------- MOBI / AZW3
def _be32(data, off):
    return struct.unpack('>I', data[off:off + 4])[0]


def _vwi(data, offset):
    """MOBI variable-width int：最高位为结束标记"""
    value = 0
    consumed = 0
    while True:
        v = data[offset + consumed]
        consumed += 1
        if v & 0x80:
            break
        value = (value << 7) | v
    value = (value << 7) | (v & 0x7F)
    return consumed, value


def _read_tag_table(data, start):
    """TAGX 段 -> (controlByteCount, [(tag, valuesPerEntry, mask, endFlag), ...])"""
    if data[start:start + 4] != b'TAGX':
        return 0, []
    first_entry_off = _be32(data, start + 4)
    control_byte_count = _be32(data, start + 8)
    tags = []
    for i in range(12, first_entry_off, 4):
        tags.append(tuple(data[start + i:start + i + 4]))
    return control_byte_count, tags


def _get_tag_map(control_byte_count, tag_table, entry_data, start_pos, end_pos):
    """解析单个索引条目的 tag -> {tag: [values]}"""
    tag_map = {}
    control_idx = 0
    data_start = start_pos + control_byte_count
    for tag, values_per_entry, mask, end_flag in tag_table:
        if end_flag == 0x01:
            control_idx += 1
            continue
        cbyte = entry_data[start_pos + control_idx]
        value = cbyte & mask
        value_count = None
        value_bytes = None
        if value != 0:
            if value == mask:
                if bin(mask).count('1') > 1:
                    consumed, value_bytes = _vwi(entry_data, data_start)
                    data_start += consumed
                else:
                    value_count = 1
            else:
                while mask & 0x01 == 0:
                    mask >>= 1
                    value >>= 1
                value_count = value
        if value_count is not None:
            values = []
            for _ in range(value_count):
                for _ in range(values_per_entry):
                    consumed, v = _vwi(entry_data, data_start)
                    data_start += consumed
                    values.append(v)
            tag_map[tag] = values
        elif value_bytes is not None:
            values = []
            consumed_total = 0
            while consumed_total < value_bytes:
                consumed, v = _vwi(entry_data, data_start)
                data_start += consumed
                consumed_total += consumed
                values.append(v)
            tag_map[tag] = values
    return tag_map


def _parse_mobi_toc(path):
    data = open(path, 'rb').read()
    if len(data) < 78:
        raise ValueError('不是有效的MOBI/PDB文件')
    rec_count = struct.unpack('>H', data[76:78])[0]
    recs = []
    p = 78
    for _ in range(rec_count):
        recs.append(struct.unpack('>I', data[p:p + 4])[0])
        p += 8
    if not recs:
        raise ValueError('PDB记录列表为空')
    rec0 = data[recs[0]:]
    if rec0[16:20] != b'MOBI':
        raise ValueError('不是有效的MOBI文件')
    length = _be32(rec0, 20)
    codepage = _be32(rec0, 28)
    codec = {1252: 'cp1252', 65001: 'utf-8', 57002: 'x-sjis'}.get(codepage, 'utf-8')

    # NCX/INDX 索引（相对 record0 起点的偏移 0xF4；旧版头无此字段）
    if length + 16 >= 0xF8:
        ncx_idx = _be32(rec0, 0xF4)
    else:
        ncx_idx = 0xFFFFFFFF
    if ncx_idx == 0xFFFFFFFF or ncx_idx >= rec_count:
        raise ValueError('该MOBI为无目录索引的旧格式，无法提取目录；建议用Calibre转换为EPUB/AZW3后重试')
    return _parse_indx_toc(data, recs, ncx_idx, codec)


def _parse_indx_toc(data, recs, idx, codec):
    """解析 INDX 索引记录中的目录（tag: 1=pos 2=len 3=文本偏移 4=层级 21=parent 22=child1 23=childn）"""
    main = data[recs[idx]:]
    if main[:4] != b'INDX':
        raise ValueError('目录索引（INDX）无效')
    words = struct.unpack('>13L', main[4:56])  # len,nul1,type,gen,start,count,code,lng,total,ordt,ligt,nligt,nctoc
    tag_section_start, index_count, nctoc = words[0], words[5], words[12]
    control_byte_count, tag_table = _read_tag_table(main, tag_section_start)
    if not tag_table:
        raise ValueError('目录索引（INDX）缺少TAGX表')

    ctoc_text = {}
    off = idx + index_count + 1
    for j in range(nctoc):
        if off + j >= len(recs):
            break
        cdata = data[recs[off + j]:]
        cpos = 0
        while cpos < len(cdata) and cdata[cpos] != 0:
            key = cpos
            consumed, ilen = _vwi(cdata, cpos)
            cpos += consumed
            name = cdata[cpos:cpos + ilen]
            cpos += ilen
            ctoc_text[key] = name.decode(codec, errors='replace')

    entries = []  # [text, hlvl, child1, childn]
    for i in range(index_count):
        rec = idx + 1 + i
        if rec >= len(recs):
            break
        rdata = data[recs[rec]:]
        if rdata[:4] != b'INDX':
            continue
        hwords = struct.unpack('>13L', rdata[4:56])
        idxt_pos, entry_count = hwords[4], hwords[5]
        positions = []
        for j in range(entry_count):
            positions.append(struct.unpack('>H', rdata[idxt_pos + 4 + 2 * j: idxt_pos + 6 + 2 * j])[0])
        positions.append(idxt_pos)
        for j in range(entry_count):
            start_pos, end_pos = positions[j], positions[j + 1]
            text_len = rdata[start_pos]
            text = rdata[start_pos + 1:start_pos + 1 + text_len]
            tag_map = _get_tag_map(control_byte_count, tag_table, rdata,
                                   start_pos + 1 + text_len, end_pos)
            entry_text = ''
            if 3 in tag_map:
                entry_text = ctoc_text.get(tag_map[3][0], '')
            if not entry_text:
                entry_text = text.decode(codec, errors='replace').strip()
            hlvl = tag_map[4][0] if 4 in tag_map else 0
            child1 = tag_map[22][0] if 22 in tag_map else -1
            childn = tag_map[23][0] if 23 in tag_map else -1
            if entry_text:
                entries.append([_clean(entry_text), hlvl, child1, childn])
    if not entries:
        raise ValueError('目录索引（INDX）中未找到目录条目')

    seq = [0]
    out = []

    def walk(start, end, lvl):
        if start < 0:
            start = 0
        if end < 0 or end > len(entries):
            end = len(entries)
        for i in range(start, end):
            e = entries[i]
            if e[1] != lvl:
                continue
            seq[0] += 1
            out.append((lvl, e[0], seq[0]))
            if e[2] >= 0:
                walk(e[2], e[3] + 1, lvl + 1)

    walk(0, len(entries), 0)
    if not out:
        raise ValueError('目录索引（INDX）层级解析失败')
    return out


def _find(elem, tag, ns):
    """带命名空间查找，失败则按无命名空间再找。
    注意：Element.__bool__ 在 Python 3.13 中基于子元素判断，不能用 `a or b` 链式查找。"""
    x = elem.find('{%s}%s' % (ns, tag))
    if x is None:
        x = elem.find(tag)
    return x


def _parse_ncx(data):
    root = ET.fromstring(data)
    navmap = _find(root, 'navMap', _NCX_NS)
    if navmap is None:
        raise ValueError('未找到 navMap')
    seq = [0]
    out = []

    def walk(navpoints, depth):
        for np in navpoints:
            label = _find(np, 'navLabel', _NCX_NS)
            title = ''
            if label is not None:
                t = _find(label, 'text', _NCX_NS)
                if t is not None:
                    title = _clean(''.join(t.itertext()))
            seq[0] += 1
            if title:
                out.append((depth, title, seq[0]))
            sub = [c for c in np if _local(c.tag) == 'navPoint']
            if sub:
                walk(sub, depth + 1)

    walk([c for c in navmap if _local(c.tag) == 'navPoint'], 0)
    if not out:
        raise ValueError('目录（navMap）为空')
    return out
