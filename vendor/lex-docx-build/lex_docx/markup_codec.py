from __future__ import annotations

from docx.oxml.ns import qn


def runs_to_markup(para_el) -> str:
    chunks = []
    for r in para_el.iter(qn('w:r')):
        txt = ''.join((t.text or '') for t in r.iter(qn('w:t')))
        if not txt:
            continue
        rPr = r.find(qn('w:rPr'))
        bold = bool(rPr is not None and rPr.find(qn('w:b')) is not None)
        italic = bool(rPr is not None and rPr.find(qn('w:i')) is not None)
        underline = bool(rPr is not None and rPr.find(qn('w:u')) is not None)
        highlight = bool(rPr is not None and rPr.find(qn('w:highlight')) is not None)
        s = txt
        if highlight:
            s = f'=={s}=='
        if underline:
            s = f'__{s}__'
        if italic:
            s = f'*{s}*'
        if bold:
            s = f'**{s}**'
        chunks.append(s)
    return ''.join(chunks)


def parse_markup(text: str):
    # minimal parser: **bold**, *italic*, __underline__, ==highlight==
    i, n = 0, len(text)
    out = []
    state = {'bold': False, 'italic': False, 'underline': False, 'highlight': False}

    def push(buf):
        if buf:
            out.append((buf, state.copy()))

    buf = ''
    while i < n:
        if text.startswith('**', i):
            push(buf); buf = ''
            state['bold'] = not state['bold']
            i += 2; continue
        if text.startswith('__', i):
            push(buf); buf = ''
            state['underline'] = not state['underline']
            i += 2; continue
        if text.startswith('==', i):
            push(buf); buf = ''
            state['highlight'] = not state['highlight']
            i += 2; continue
        if text[i] == '*':
            push(buf); buf = ''
            state['italic'] = not state['italic']
            i += 1; continue
        buf += text[i]
        i += 1
    push(buf)
    return out
