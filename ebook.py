# -*- coding: utf-8 -*-
"""电子书内置目录提取：EPUB / MOBI / AZW3 / PRC

输出与 PDF 书签 txt 兼容：行首缩进定层级，行尾 [p序号]。
电子书没有页码，[p序号] 为该目录项在书中出现的阅读顺序号（1 起），
可用于查看/整理目录；如需写入 PDF，请将序号替换为真实 PDF 页号。

实现仅用标准库（zipfile + xml + struct），不引入额外依赖。
"""
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

BOOK_EXTS = {'.epub', '.mobi', '.azw3', '.prc', '.azw'}

_NCX_NS = 'http://www.daisy.org/z3986/2005/ncx/'
_XHTML_NS = 'http://www.w3.org/1999/xhtml'
_OPF_NS = 'http://www.idpf.org/2007/opf'
_OPS_NS = 'http://www.idpf.org/2007/ops'
_CONTAINER_NS = 'urn:oasis:names:tc:opendocument:xmlns:container'

# ---- MOBI/PDB 二进制布局常量 ----
MOBI_PDB_HEADER_LENGTH = 78       # PDB 头部长度（记录数偏移 76）
MOBI_RECORD_TABLE_OFFSET = 76
MOBI_HEADER_OFFSET_LENGTH = 20    # MOBI 头内 header 长度字段偏移
MOBI_HEADER_OFFSET_CODEPAGE = 28  # MOBI 头内代码页字段偏移
MOBI_NCX_INDEX_OFFSET = 0xF4      # NCX/INDX 索引字段相对 MOBI 头起点偏移
MOBI_HEADER_MIN_LENGTH_WITH_NCX = 0xF8  # 含 NCX 索引字段所需最小头部长度
MOBI_MAGIC = b'MOBI'
INDX_MAGIC = b'INDX'
TAGX_MAGIC = b'TAGX'
MOBI_TAG_POS = 1       # 目录条目的位置 tag
MOBI_TAG_LEN = 2       # 文本长度 tag
MOBI_TAG_TEXT_OFFSET = 3   # 文本偏移（CTOC 表中的 key）tag
MOBI_TAG_LEVEL = 4     # 标题层级 tag
MOBI_TAG_FIRST_CHILD = 22  # 第一个子条目索引 tag
MOBI_TAG_LAST_CHILD = 23   # 最后一个子条目索引（含）tag
CODEPAGE_ENCODING_MAP = {1252: 'cp1252', 65001: 'utf-8', 57002: 'x-sjis'}


def is_ebook(path: str) -> bool:
    """判断文件扩展名是否为支持的电子书格式"""
    return os.path.splitext(path)[1].lower() in BOOK_EXTS


def extract_toc(path: str) -> List[Tuple[int, str, int]]:
    """提取电子书内置目录 -> [(indent, title, seq)]，indent>=0，seq>=1"""
    file_extension = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        raise ValueError('文件不存在: %s' % path)
    if file_extension == '.epub':
        return _epub_toc(path)
    if file_extension in ('.mobi', '.azw3', '.prc', '.azw'):
        return _parse_mobi_toc(path)
    raise ValueError('不支持的电子书格式: %s（支持 %s）'
                     % (file_extension, ' / '.join(sorted(BOOK_EXTS))))


def to_txt(toc_entries: List[Tuple[int, str, int]],
           with_sequence: bool = True) -> str:
    """目录条目 -> txt 文本（缩进 + 标题；with_sequence 时行尾带 [p序号]）"""
    output_lines = []
    for indent, title, sequence_number in toc_entries:
        suffix = ' [p%d]' % sequence_number if with_sequence else ''
        output_lines.append('  ' * indent + title + suffix)
    return '\n'.join(output_lines) + '\n'


def _strip_namespace(tag: str) -> str:
    """取 XML 标签去掉命名空间后的本地名（{ns}tag -> tag）"""
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _clean_title(title: Optional[str]) -> str:
    """清洗标题：压缩空白并去掉首尾空白/零宽字符"""
    return re.sub(r'\s+', ' ', title or '').strip(' \t\r\n\ufeff')


# ---------------------------------------------------------------- EPUB
def _epub_toc(path: str) -> List[Tuple[int, str, int]]:
    """EPUB 目录：优先 nav 文档，其次 NCX"""
    with zipfile.ZipFile(path) as archive:
        try:
            container_xml = archive.read('META-INF/container.xml')
        except KeyError:
            raise ValueError('不是有效的EPUB文件（缺少 META-INF/container.xml）')
        container_root = ET.fromstring(container_xml)
        rootfile = container_root.find('.//{%s}rootfile' % _CONTAINER_NS)
        if rootfile is None:
            raise ValueError('EPUB容器中未找到 rootfile')
        opf_path = rootfile.get('full-path') or ''
        opf_directory = os.path.dirname(opf_path).replace('\\', '/')
        opf_root = ET.fromstring(archive.read(opf_path))

        nav_path = None
        ncx_identifier = None
        manifest_paths: Dict[str, Optional[str]] = {}
        for manifest_item in opf_root.findall('.//{%s}item' % _OPF_NS):
            item_id = manifest_item.get('id')
            href = manifest_item.get('href')
            manifest_paths[item_id] = href
            properties = (manifest_item.get('properties') or '').split()
            if 'nav' in properties:
                nav_path = href
        spine = opf_root.find('.//{%s}spine' % _OPF_NS)
        if spine is not None:
            ncx_identifier = spine.get('toc')
        ncx_path = manifest_paths.get(ncx_identifier) if ncx_identifier else None
        for manifest_item in opf_root.findall('.//{%s}item' % _OPF_NS):
            if manifest_item.get('media-type') == 'application/x-dtbncx+xml':
                ncx_path = manifest_item.get('href')

        toc_path = nav_path or ncx_path
        if not toc_path:
            raise ValueError('EPUB中未找到目录文件（nav/ncx）')
        full_path = opf_directory + '/' + toc_path if opf_directory else toc_path
        file_bytes = archive.read(full_path.lstrip('/'))
        if nav_path:
            return _parse_nav_html(file_bytes)
        return _parse_ncx(file_bytes)


def _parse_nav_html(file_bytes: bytes) -> List[Tuple[int, str, int]]:
    """解析 EPUB3 nav 文档：取 type=toc 的 nav，按 <ol>/<li> 层级递归"""
    root = ET.fromstring(file_bytes)
    toc_navigation = None
    for nav_element in root.iter('{%s}nav' % _XHTML_NS):
        if nav_element.get('{%s}type' % _OPS_NS) == 'toc':
            toc_navigation = nav_element
            break
    if toc_navigation is None:
        all_navigations = list(root.iter('{%s}nav' % _XHTML_NS))
        if all_navigations:
            toc_navigation = all_navigations[0]
    if toc_navigation is None:
        raise ValueError('未找到目录（nav）')
    sequence_counter = [0]
    toc_entries: List[Tuple[int, str, int]] = []

    def walk_list(list_element: ET.Element, depth: int) -> None:
        for list_item in list_element.findall('{%s}li' % _XHTML_NS):
            anchor_element = list_item.find('{%s}a' % _XHTML_NS)
            if anchor_element is not None:
                sequence_counter[0] += 1
                title = _clean_title(''.join(anchor_element.itertext()))
                if title:
                    toc_entries.append((depth, title, sequence_counter[0]))
            for child_element in list_item:
                if _strip_namespace(child_element.tag) == 'ol':
                    walk_list(child_element, depth + 1)

    for list_element in toc_navigation.findall('{%s}ol' % _XHTML_NS):
        walk_list(list_element, 0)
    if not toc_entries:
        raise ValueError('目录（nav）为空')
    return toc_entries


# ---------------------------------------------------------------- MOBI / AZW3
def _be32(file_bytes: bytes, offset: int) -> int:
    """读取大端 4 字节无符号整数"""
    return struct.unpack('>I', file_bytes[offset:offset + 4])[0]


def _vwi(file_bytes: bytes, offset: int) -> Tuple[int, int]:
    """MOBI variable-width int：最高位为结束标记 -> (consumed_bytes, value)"""
    accumulated = 0
    consumed_bytes = 0
    while True:
        byte_value = file_bytes[offset + consumed_bytes]
        consumed_bytes += 1
        if byte_value & 0x80:
            break
        accumulated = (accumulated << 7) | byte_value
    accumulated = (accumulated << 7) | (byte_value & 0x7F)
    return consumed_bytes, accumulated


def _read_tag_table(file_bytes: bytes, section_offset: int
                    ) -> Tuple[int, List[Tuple[int, int, int, int]]]:
    """TAGX 段 -> (control_byte_count, [(tag, values_per_entry, mask, end_flag), ...])

    无 TAGX 时返回 (0, [])，调用方据此判定索引缺表。
    """
    if file_bytes[section_offset:section_offset + 4] != TAGX_MAGIC:
        return 0, []
    first_entry_offset = _be32(file_bytes, section_offset + 4)
    control_byte_count = _be32(file_bytes, section_offset + 8)
    tag_definitions: List[Tuple[int, int, int, int]] = []
    for table_offset in range(12, first_entry_offset, 4):
        tag_definitions.append(tuple(file_bytes[section_offset + table_offset:
                                                section_offset + table_offset + 4]))
    return control_byte_count, tag_definitions


def _get_tag_map(control_byte_count: int,
                 tag_table: List[Tuple[int, int, int, int]],
                 entry_bytes: bytes, entry_start: int, entry_end: int
                 ) -> Dict[Tuple[int, int, int, int], List[int]]:
    """解析单个索引条目的 tag -> {tag: [values]}（values 为 VWI 解码后的数）"""
    tag_values_map: Dict[Tuple[int, int, int, int], List[int]] = {}
    control_index = 0
    data_cursor = entry_start + control_byte_count
    for tag, values_per_entry, mask, end_flag in tag_table:
        if end_flag == 0x01:
            control_index += 1
            continue
        control_byte = entry_bytes[entry_start + control_index]
        field_value = control_byte & mask
        value_count = None
        byte_length = None
        if field_value != 0:
            if field_value == mask:
                # 值满了：多字节 VWI 存储（大字段）；单 bit 掩码则固定为 1 个值
                if bin(mask).count('1') > 1:
                    consumed_bytes, byte_length = _vwi(entry_bytes, data_cursor)
                    data_cursor += consumed_bytes
                else:
                    value_count = 1
            else:
                # 掩码位数可能不足，先右移到最低位再取值
                while mask & 0x01 == 0:
                    mask >>= 1
                    field_value >>= 1
                value_count = field_value
        if value_count is not None:
            decoded_values = []
            for _ in range(value_count):
                for _ in range(values_per_entry):
                    consumed_bytes, raw_value = _vwi(entry_bytes, data_cursor)
                    data_cursor += consumed_bytes
                    decoded_values.append(raw_value)
            tag_values_map[tag] = decoded_values
        elif byte_length is not None:
            decoded_values = []
            consumed_total = 0
            while consumed_total < byte_length:
                consumed_bytes, raw_value = _vwi(entry_bytes, data_cursor)
                data_cursor += consumed_bytes
                consumed_total += consumed_bytes
                decoded_values.append(raw_value)
            tag_values_map[tag] = decoded_values
    return tag_values_map


def _parse_mobi_toc(path: str) -> List[Tuple[int, str, int]]:
    """解析 MOBI/AZW3 的 INDX 目录索引"""
    with open(path, 'rb') as mobi_file:
        file_bytes = mobi_file.read()
    if len(file_bytes) < MOBI_PDB_HEADER_LENGTH:
        raise ValueError('不是有效的MOBI/PDB文件')
    record_count = struct.unpack('>H', file_bytes[MOBI_RECORD_TABLE_OFFSET:
                                                  MOBI_RECORD_TABLE_OFFSET + 2])[0]
    record_offsets: List[int] = []
    record_pointer = MOBI_PDB_HEADER_LENGTH
    for _ in range(record_count):
        record_offsets.append(struct.unpack('>I', file_bytes[record_pointer:record_pointer + 4])[0])
        record_pointer += 8
    if not record_offsets:
        raise ValueError('PDB记录列表为空')
    record_zero_bytes = file_bytes[record_offsets[0]:]
    if record_zero_bytes[16:20] != MOBI_MAGIC:
        raise ValueError('不是有效的MOBI文件')
    header_length = _be32(record_zero_bytes, MOBI_HEADER_OFFSET_LENGTH)
    codepage_id = _be32(record_zero_bytes, MOBI_HEADER_OFFSET_CODEPAGE)
    encoding_name = CODEPAGE_ENCODING_MAP.get(codepage_id, 'utf-8')

    # NCX/INDX 索引（相对 record0 起点的偏移 0xF4；旧版头无此字段）
    if header_length + 16 >= MOBI_HEADER_MIN_LENGTH_WITH_NCX:
        ncx_index = _be32(record_zero_bytes, MOBI_NCX_INDEX_OFFSET)
    else:
        ncx_index = 0xFFFFFFFF
    if ncx_index == 0xFFFFFFFF or ncx_index >= record_count:
        raise ValueError('该MOBI为无目录索引的旧格式，无法提取目录；建议用Calibre转换为EPUB/AZW3后重试')
    return _parse_indx_toc(file_bytes, record_offsets, ncx_index, encoding_name)


def _parse_indx_toc(file_bytes: bytes, record_offsets: List[int], ncx_index: int,
                    encoding_name: str) -> List[Tuple[int, str, int]]:
    """解析 INDX 索引记录中的目录（tag: 1=pos 2=len 3=文本偏移 4=层级 21=parent 22=child1 23=childn）"""
    main_record = file_bytes[record_offsets[ncx_index]:]
    if main_record[:4] != INDX_MAGIC:
        raise ValueError('目录索引（INDX）无效')
    header_fields = struct.unpack('>13L', main_record[4:56])
    tag_section_offset, index_count, nctoc = header_fields[0], header_fields[5], header_fields[12]
    control_byte_count, tag_table = _read_tag_table(main_record, tag_section_offset)
    if not tag_table:
        raise ValueError('目录索引（INDX）缺少TAGX表')

    # CTOC 表：KEY(文本偏移) -> 解码文本
    ctoc_text_map: Dict[int, str] = {}
    ctoc_record_start = ncx_index + index_count + 1
    for record_index in range(nctoc):
        if ctoc_record_start + record_index >= len(record_offsets):
            break
        ctoc_bytes = file_bytes[record_offsets[ctoc_record_start + record_index]:]
        cursor = 0
        while cursor < len(ctoc_bytes) and ctoc_bytes[cursor] != 0:
            key = cursor
            consumed_bytes, text_length = _vwi(ctoc_bytes, cursor)
            cursor += consumed_bytes
            title_bytes = ctoc_bytes[cursor:cursor + text_length]
            cursor += text_length
            ctoc_text_map[key] = title_bytes.decode(encoding_name, errors='replace')

    entry_records: List[Tuple[str, int, int, int]] = []  # (文本, 层级, 首子索引, 末子索引)
    for entry_index in range(index_count):
        record_index = ncx_index + 1 + entry_index
        if record_index >= len(record_offsets):
            break
        record_bytes = file_bytes[record_offsets[record_index]:]
        if record_bytes[:4] != INDX_MAGIC:
            continue
        record_header_fields = struct.unpack('>13L', record_bytes[4:56])
        idxt_section_offset, entry_count = record_header_fields[4], record_header_fields[5]
        entry_positions: List[int] = []
        for position_index in range(entry_count):
            entry_positions.append(struct.unpack(
                '>H', record_bytes[idxt_section_offset + 4 + 2 * position_index:
                                   idxt_section_offset + 6 + 2 * position_index])[0])
        entry_positions.append(idxt_section_offset)
        for position_index in range(entry_count):
            entry_start, entry_end = entry_positions[position_index], entry_positions[position_index + 1]
            text_length = record_bytes[entry_start]
            inline_text = record_bytes[entry_start + 1:entry_start + 1 + text_length]
            tag_values_map = _get_tag_map(control_byte_count, tag_table, record_bytes,
                                          entry_start + 1 + text_length, entry_end)
            entry_title = ''
            if MOBI_TAG_TEXT_OFFSET in tag_values_map:
                entry_title = ctoc_text_map.get(tag_values_map[MOBI_TAG_TEXT_OFFSET][0], '')
            if not entry_title:
                entry_title = inline_text.decode(encoding_name, errors='replace').strip()
            heading_level = tag_values_map[MOBI_TAG_LEVEL][0] if MOBI_TAG_LEVEL in tag_values_map else 0
            first_child_index = tag_values_map[MOBI_TAG_FIRST_CHILD][0] \
                if MOBI_TAG_FIRST_CHILD in tag_values_map else -1
            last_child_index = tag_values_map[MOBI_TAG_LAST_CHILD][0] \
                if MOBI_TAG_LAST_CHILD in tag_values_map else -1
            if entry_title:
                entry_records.append((_clean_title(entry_title), heading_level,
                                      first_child_index, last_child_index))
    if not entry_records:
        raise ValueError('目录索引（INDX）中未找到目录条目')

    sequence_counter = [0]
    toc_entries: List[Tuple[int, str, int]] = []

    def walk_children(start_index: int, end_index: int, level: int) -> None:
        if start_index < 0:
            start_index = 0
        if end_index < 0 or end_index > len(entry_records):
            end_index = len(entry_records)
        for entry_index in range(start_index, end_index):
            entry_title, heading_level, first_child_index, last_child_index = entry_records[entry_index]
            if heading_level != level:
                continue
            sequence_counter[0] += 1
            toc_entries.append((level, entry_title, sequence_counter[0]))
            if first_child_index >= 0:
                walk_children(first_child_index, last_child_index + 1, level + 1)

    walk_children(0, len(entry_records), 0)
    if not toc_entries:
        raise ValueError('目录索引（INDX）层级解析失败')
    return toc_entries


def _find(element: ET.Element, tag: str, namespace: str) -> Optional[ET.Element]:
    """带命名空间查找，失败则按无命名空间再找。

    注意：Element.__bool__ 在 Python 3.13 中基于子元素判断，不能用 `a or b` 链式查找。
    """
    found = element.find('{%s}%s' % (namespace, tag))
    if found is None:
        found = element.find(tag)
    return found


def _parse_ncx(file_bytes: bytes) -> List[Tuple[int, str, int]]:
    """解析 EPUB2 NCX 的 navMap"""
    root = ET.fromstring(file_bytes)
    navmap = _find(root, 'navMap', _NCX_NS)
    if navmap is None:
        raise ValueError('未找到 navMap')
    sequence_counter = [0]
    toc_entries: List[Tuple[int, str, int]] = []

    def walk_navpoints(nav_points, depth: int) -> None:
        for nav_point in nav_points:
            label = _find(nav_point, 'navLabel', _NCX_NS)
            title = ''
            if label is not None:
                text_element = _find(label, 'text', _NCX_NS)
                if text_element is not None:
                    title = _clean_title(''.join(text_element.itertext()))
            sequence_counter[0] += 1
            if title:
                toc_entries.append((depth, title, sequence_counter[0]))
            child_navpoints = [child for child in nav_point if _strip_namespace(child.tag) == 'navPoint']
            if child_navpoints:
                walk_navpoints(child_navpoints, depth + 1)

    walk_navpoints([child for child in navmap if _strip_namespace(child.tag) == 'navPoint'], 0)
    if not toc_entries:
        raise ValueError('目录（navMap）为空')
    return toc_entries
