# -*- coding: utf-8 -*-
import json, re, sys

def read_lines(page_boxes):
    """cluster boxes into visual lines by y; return list of dict(x, y, title_frags, num_frags)"""
    boxes = sorted(page_boxes, key=lambda d: (d['y'], d['x']))
    lines = []
    for b in boxes:
        if not lines or abs(b['y'] - lines[-1]['y']) > 40:
            lines.append({'y': b['y'], 'x': b['x'], 'tf': [], 'nf': []})
        ln = lines[-1]
        ln['y'] = (ln['y'] + b['y']) / 2
        if b['x'] >= 1600:
            ln['nf'].append(b['text'])
        else:
            ln['tf'].append((b['x'], b['text']))
    for ln in lines:
        ln['tf'].sort()
        ln['title'] = ''.join(t for _, t in ln['tf'])
        ln['numtxt'] = ''.join(ln['nf'])
        ln['x'] = ln['tf'][0][0] if ln['tf'] else (ln['x'] if ln['nf'] else 0)
    return lines

SEC_MARK = re.compile(r'^[（(]?[一二三四五六七八九十0-9]+[、.，,:）)]|^第[一二三四五六七八九十0-9]+[编章节]|^附录|^结束语|^序言|^书后|^再论|^注释|^人名索引|^附')

def parse_toc_json(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    entries = []
    for pg in data:
        lines = read_lines(pg['lines'])
        for ln in lines:
            t = ln['title']
            m = re.search(r'([0-9]+)[-—–~]?([0-9]*)\s*$', t)
            embedded = None
            if m and re.search(r'[0-9]{2,}', m.group(0)):
                embedded = m.group(0)
                t = t[:m.start()].rstrip('·.')
            numtxt = (ln['numtxt'] + ' ' + (embedded or '')).strip()
            has_num = bool(numtxt)
            if has_num and not entries:
                # first entry: printed page often omitted (starts at 1)
                entries.append({'title': t, 'x': ln['x'], 'numtxt': numtxt, 'has_num': True, 'is_first': True})
            elif has_num:
                entries.append({'title': t, 'x': ln['x'], 'numtxt': numtxt, 'has_num': True, 'is_first': False})
            elif not has_num and entries:
                # no number: continuation of previous multi-line title OR standalone heading
                prev = entries[-1]
                if prev['x'] + 40 <= ln['x'] and not SEC_MARK.match(t):
                    prev['title'] += t
                else:
                    entries.append({'title': t, 'x': ln['x'], 'numtxt': '', 'has_num': False, 'is_first': False})
    return entries

def extract_pages(e):
    nums = [int(n) for n in re.findall(r'\d+', e['numtxt'])]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return nums[0], nums[-1]

def classify(entries):
    out = []
    for e in entries:
        if e['title'].startswith('1894') or e['title'].startswith('日录') or e['title'].startswith('目录'):
            continue
        s, en = extract_pages(e)
        x = e['x']
        lvl = 0 if x < 330 else (1 if x < 480 else 2)
        if e.get('is_first') and s is not None:
            s = 1
        out.append({'title': e['title'], 'lvl': lvl, 'start': s, 'end': en, 'x': x})
    return out

if __name__ == '__main__':
    entries = parse_toc_json(sys.argv[1])
    out = classify(entries)
    for o in out:
        rng = (str(o['start']) + '-' + str(o['end'])) if o['start'] else ''
        print('L%d x=%3d %-60s %s' % (o['lvl'], o['x'], o['title'][:58], rng))
