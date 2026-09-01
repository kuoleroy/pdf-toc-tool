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
    # 按扩展名分发：EPUB 走 zip 容器解析，MOBI 系（含 AZW3/PRC/AZW）走 PDB/INDX 二进制解析
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
        # 行尾 [p序号] 与 PDF 书签 txt 格式一致：序号为该条目在书中的阅读顺序号
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
            # EPUB 本质是 zip 包：META-INF/container.xml 描述容器根，指向真正的 OPF 元数据文件
            container_xml = archive.read('META-INF/container.xml')
        except KeyError:
            raise ValueError('不是有效的EPUB文件（缺少 META-INF/container.xml）')
        container_root = ET.fromstring(container_xml)
        rootfile = container_root.find('.//{%s}rootfile' % _CONTAINER_NS)
        if rootfile is None:
            raise ValueError('EPUB容器中未找到 rootfile')
        opf_path = rootfile.get('full-path') or ''
        # 记录 OPF 所在目录，用于把 manifest 中的相对 href 拼成包内完整路径
        opf_directory = os.path.dirname(opf_path).replace('\\', '/')
        opf_root = ET.fromstring(archive.read(opf_path))

        nav_path = None
        ncx_identifier = None
        manifest_paths: Dict[str, Optional[str]] = {}
        for manifest_item in opf_root.findall('.//{%s}item' % _OPF_NS):
            item_id = manifest_item.get('id')
            href = manifest_item.get('href')
            manifest_paths[item_id] = href
            # EPUB3 的导航文档在 manifest 中以 properties="nav" 标记
            properties = (manifest_item.get('properties') or '').split()
            if 'nav' in properties:
                nav_path = href
        # EPUB2 的目录为 NCX 文件：spine 的 toc 属性给出其 manifest id
        spine = opf_root.find('.//{%s}spine' % _OPF_NS)
        if spine is not None:
            ncx_identifier = spine.get('toc')
        ncx_path = manifest_paths.get(ncx_identifier) if ncx_identifier else None
        # 兜底：部分电子书未在 spine 声明 toc，按 NCX 专用 media-type 再找一次
        for manifest_item in opf_root.findall('.//{%s}item' % _OPF_NS):
            if manifest_item.get('media-type') == 'application/x-dtbncx+xml':
                ncx_path = manifest_item.get('href')

        # 目录来源优先级：EPUB3 nav > EPUB2 NCX
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
    # 标准要求 type="toc" 的 nav 才是目录；个别电子书未标注，则退而取第一个 nav
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
    # 可变容器用于在递归中共享计数：遍历顺序即阅读顺序，[p序号] 按此递增
    sequence_counter = [0]
    toc_entries: List[Tuple[int, str, int]] = []

    def walk_list(list_element: ET.Element, depth: int) -> None:
        for list_item in list_element.findall('{%s}li' % _XHTML_NS):
            # <a> 取标题并计数；嵌套 <ol> 递归下降一层，depth 即缩进层级
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
    # 变长整数：每字节低 7 位为数据，最高位为 0 表示还有后续字节
    accumulated = 0
    consumed_bytes = 0
    while True:
        byte_value = file_bytes[offset + consumed_bytes]
        consumed_bytes += 1
        if byte_value & 0x80:
            break
        accumulated = (accumulated << 7) | byte_value
    # 末字节最高位为 1（结束标记），仅取低 7 位拼接
    accumulated = (accumulated << 7) | (byte_value & 0x7F)
    return consumed_bytes, accumulated


def _read_tag_table(file_bytes: bytes, section_offset: int
                    ) -> Tuple[int, List[Tuple[int, int, int, int]]]:
    """TAGX 段 -> (control_byte_count, [(tag, values_per_entry, mask, end_flag), ...])

    无 TAGX 时返回 (0, [])，调用方据此判定索引缺表。
    """
    if file_bytes[section_offset:section_offset + 4] != TAGX_MAGIC:
        return 0, []
    # TAGX 段布局：魔数(4) + 首条 tag 定义偏移(4) + 控制字节数(4)，其后每条 4 字节定义 tag 字段
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
    # 每条目的前 control_byte_count 字节是控制字节：按位掩码描述各 tag 的字段值
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
    # PDB 头：偏移 76 处为大端 2 字节记录数；其后每记录 8 字节（4 字节偏移 + 4 字节属性）
    record_count = struct.unpack('>H', file_bytes[MOBI_RECORD_TABLE_OFFSET:
                                                  MOBI_RECORD_TABLE_OFFSET + 2])[0]
    record_offsets: List[int] = []
    record_pointer = MOBI_PDB_HEADER_LENGTH
    for _ in range(record_count):
        record_offsets.append(struct.unpack('>I', file_bytes[record_pointer:record_pointer + 4])[0])
        record_pointer += 8
    if not record_offsets:
        raise ValueError('PDB记录列表为空')
    # 0 号记录是 MOBI 头（其后紧跟 PalmDOC 文本流），其偏移 16 处应为 "MOBI" 魔数
    record_zero_bytes = file_bytes[record_offsets[0]:]
    if record_zero_bytes[16:20] != MOBI_MAGIC:
        raise ValueError('不是有效的MOBI文件')
    header_length = _be32(record_zero_bytes, MOBI_HEADER_OFFSET_LENGTH)
    codepage_id = _be32(record_zero_bytes, MOBI_HEADER_OFFSET_CODEPAGE)
    # 按 MOBI 头声明的代码页选择解码编码，未知代码页回退 utf-8
    encoding_name = CODEPAGE_ENCODING_MAP.get(codepage_id, 'utf-8')

    # NCX/INDX 索引记录号位于 record0 偏移 0xF4；仅新版（头部足够长）才有该字段
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
    # INDX 头 4..56 字节为 13 个大端字段：0=TAGX段偏移 5=索引条目数 12=CTOC 记录数
    header_fields = struct.unpack('>13L', main_record[4:56])
    tag_section_offset, index_count, nctoc = header_fields[0], header_fields[5], header_fields[12]
    control_byte_count, tag_table = _read_tag_table(main_record, tag_section_offset)
    if not tag_table:
        raise ValueError('目录索引（INDX）缺少TAGX表')

    # CTOC 表：目录文本池，key 为文本在记录内的字节偏移（VWI 长度前缀 + 文本 连续存放）
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

    # 每个 INDX 记录含 IDXT 偏移表，指向若干条目录条目；(文本, 层级, 首子索引, 末子索引)
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
        # 追加段结束偏移作为哨兵，用于界定每条目的起始/结束字节位置
        entry_positions.append(idxt_section_offset)
        for position_index in range(entry_count):
            entry_start, entry_end = entry_positions[position_index], entry_positions[position_index + 1]
            # 条目内嵌短文本（1 字节长度前缀），长文本统一放 CTOC 表按偏移引用
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

    # 按"首子/末子条目索引"区间递归重建树：level 即缩进层级，计数顺序即 [p序号]
    def walk_children(start_index: int, end_index: int, level: int) -> None:
        if start_index < 0:
            start_index = 0
        if end_index < 0 or end_index > len(entry_records):
            end_index = len(entry_records)
        for entry_index in range(start_index, end_index):
            entry_title, heading_level, first_child_index, last_child_index = entry_records[entry_index]
            # 仅处理与当前层级匹配的条目，避免重复遍历父级区间内的子级条目
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

    # navPoint 可嵌套：递归下降一层 depth+1（对应输出缩进），遍历顺序即阅读顺序
    def walk_navpoints(nav_points, depth: int) -> None:
        for nav_point in nav_points:
            # 标题取自 navLabel/text 元素；计数对所有 navPoint 递增，标题为空也占序号
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


# ---- EPUB 合并 ----

def merge_epubs(epub_paths: List[str], output_path: str,
                title: str = '', author: str = '') -> str:
    """将多本EPUB合并为一本，自动生成合并目录。
    使用zipfile直接操作，绕过ebooklib的资源校验。
    
    Args:
        epub_paths: EPUB文件路径列表（按顺序合并）
        output_path: 输出EPUB文件路径
        title: 合并后的书名，默认取第一本的书名
        author: 合并后的作者，默认取第一本的作者
    
    Returns:
        输出文件路径
    """
    if not epub_paths:
        raise ValueError('请至少选择一本EPUB')
    
    for path in epub_paths:
        if not os.path.isfile(path):
            raise ValueError('文件不存在: %s' % path)
        if not path.lower().endswith('.epub'):
            raise ValueError('不是EPUB文件: %s' % path)
    
    # 解压所有EPUB到临时目录，然后合并
    import tempfile
    import shutil
    import zipfile
    
    work_dir = tempfile.mkdtemp()
    output_dir = os.path.join(work_dir, 'merged')
    merged_opf_dir = os.path.join(output_dir, 'OEBPS')
    os.makedirs(merged_opf_dir, exist_ok=True)
    
    try:
        # 读取第一本书获取元数据
        first_epub = epub_paths[0]
        with zipfile.ZipFile(first_epub, 'r') as z:
            # 找OPF文件
            opf_path = _find_opf_in_zip(z)
            if opf_path:
                opf_content = z.read(opf_path).decode('utf-8', errors='ignore')
                # 简单提取title和creator
                import re
                title_match = re.search(r'<dc:title[^>]*>(.*?)</dc:title>', opf_content, re.DOTALL)
                creator_match = re.search(r'<dc:creator[^>]*>(.*?)</dc:creator>', opf_content, re.DOTALL)
                
                if not title and title_match:
                    title = title_match.group(1).strip()
                if not author and creator_match:
                    author = creator_match.group(1).strip()
        
        if not title:
            title = os.path.splitext(os.path.basename(first_epub))[0]
        if not author:
            author = '未知作者'
        
        # 收集所有文件
        all_files = {}  # new_path -> content
        spine_order = []  # XHTML文件顺序
        manifest_items = []  # manifest条目
        toc_items = []  # 目录条目
        
        for book_idx, epub_path in enumerate(epub_paths):
            with zipfile.ZipFile(epub_path, 'r') as z:
                opf_path = _find_opf_in_zip(z)
                if not opf_path:
                    continue
                
                opf_dir = os.path.dirname(opf_path)
                opf_content = z.read(opf_path).decode('utf-8', errors='ignore')
                
                # 解析spine顺序
                book_spine = _parse_opf_spine(z, opf_path)
                book_title = os.path.basename(epub_path).replace('.epub', '')
                
                # 添加分隔页
                sep_html = '<html><head><title>%s</title></head><body><h1>%s</h1><hr/></body></html>' % (book_title, book_title)
                sep_name = 'Text/separator_%d.xhtml' % book_idx
                all_files[sep_name] = sep_html.encode('utf-8')
                spine_order.append(sep_name)
                manifest_items.append((sep_name, 'application/xhtml+xml', 'chapter%d' % book_idx))
                toc_items.append((book_title, sep_name))
                
                # 复制所有资源
                for item_name in z.namelist():
                    if item_name.endswith('/') or item_name == opf_path:
                        continue
                    
                    # 跳过META-INF
                    if 'META-INF' in item_name:
                        continue
                    
                    # 确定新文件名
                    rel_path = item_name
                    if opf_dir and item_name.startswith(opf_dir + '/'):
                        rel_path = item_name[len(opf_dir)+1:]
                    
                    new_path = 'book%d_%s' % (book_idx, rel_path)
                    
                    try:
                        content = z.read(item_name)
                        all_files[new_path] = content
                        
                        # 根据扩展名确定媒体类型
                        ext = os.path.splitext(rel_path)[1].lower()
                        media_type = _get_media_type(ext)
                        if media_type:
                            manifest_items.append((new_path, media_type, None))
                            if ext in ('.xhtml', '.html', '.htm'):
                                spine_order.append(new_path)
                    except Exception:
                        continue
        
        # 生成content.opf
        opf_content = _generate_opf(title, author, manifest_items, spine_order)
        all_files['OEBPS/content.opf'] = opf_content.encode('utf-8')
        
        # 生成toc.ncx
        ncx_content = _generate_ncx(title, toc_items)
        all_files['OEBPS/toc.ncx'] = ncx_content.encode('utf-8')
        
        # 生成container.xml
        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''
        all_files['META-INF/container.xml'] = container_xml.encode('utf-8')
        
        # 写入输出EPUB
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for path, content in all_files.items():
                zout.writestr(path, content)
        
        return output_path
        
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _find_opf_in_zip(z):
    """在ZIP中找到OPF文件路径"""
    import zipfile
    # 首先检查container.xml
    if 'META-INF/container.xml' in z.namelist():
        container = z.read('META-INF/container.xml').decode('utf-8', errors='ignore')
        import re
        match = re.search(r'full-path="([^"]+\.opf)"', container)
        if match:
            return match.group(1)
    
    # 查找任何.opf文件
    for name in z.namelist():
        if name.endswith('.opf'):
            return name
    return None


def _parse_opf_spine(z, opf_path):
    """从OPF解析spine顺序"""
    import re
    try:
        opf_content = z.read(opf_path).decode('utf-8', errors='ignore')
        # 提取spine中的itemref idref
        spine_matches = re.findall(r'<itemref\s+idref="([^"]+)"', opf_content)
        # 提取manifest中的id->href映射
        manifest_matches = re.findall(r'<item\s+[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf_content)
        id_to_href = dict(manifest_matches)
        return [id_to_href.get(idref, idref) for idref in spine_matches]
    except Exception:
        return []


def _get_media_type(ext):
    """根据扩展名获取媒体类型"""
    types = {
        '.xhtml': 'application/xhtml+xml',
        '.html': 'application/xhtml+xml',
        '.htm': 'application/xhtml+xml',
        '.css': 'text/css',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.js': 'application/javascript',
        '.ttf': 'application/font-sfnt',
        '.otf': 'application/font-sfnt',
        '.woff': 'application/font-woff',
        '.woff2': 'font/woff2',
    }
    return types.get(ext)


def _generate_opf(title, author, manifest_items, spine_order):
    """生成content.opf内容"""
    opf = '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>%s</dc:title>
    <dc:creator>%s</dc:creator>
    <dc:language>zh</dc:language>
    <dc:identifier id="BookId">merged_%d</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
''' % (title, author, int(__import__('time').time()))
    
    for i, (href, media_type, uid) in enumerate(manifest_items):
        item_id = uid or 'item%d' % i
        opf += '    <item id="%s" href="%s" media-type="%s"/>\n' % (item_id, href, media_type)
    
    opf += '''  </manifest>
  <spine toc="ncx">
'''
    for href in spine_order:
        # 找到对应的item id
        item_id = None
        for i, (h, _, uid) in enumerate(manifest_items):
            if h == href:
                item_id = uid or 'item%d' % i
                break
        if item_id:
            opf += '    <itemref idref="%s"/>\n' % item_id
    
    opf += '''  </spine>
</package>'''
    return opf


def _generate_ncx(title, toc_items):
    """生成toc.ncx内容"""
    ncx = '''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="merged"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>%s</text></docTitle>
  <navMap>
''' % title
    
    for i, (nav_title, href) in enumerate(toc_items):
        ncx += '''    <navPoint id="navPoint-%d" playOrder="%d">
      <navLabel><text>%s</text></navLabel>
      <content src="%s"/>
    </navPoint>
''' % (i+1, i+1, nav_title, href)
    
    ncx += '''  </navMap>
</ncx>'''
    return ncx
