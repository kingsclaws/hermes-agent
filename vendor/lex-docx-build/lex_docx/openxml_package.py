"""
openxml_package.py — lightweight OpenXML document wrapper for lex_docx.

目标：
- 让 tc/review/comment/footer/header 这类底层 XML 任务不再依赖 python-docx Document 作为真相层。
- 直接基于 OPC(zip) + WordprocessingML(OpenXML) 读取、修改、保存文档。
- 提供与现有 lex_docx 代码兼容的最小接口：doc.element.body / doc.paragraphs / doc.part.rels / doc.save().
"""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO

from docx.oxml.ns import qn
from lxml import etree

PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass
class OpenXmlParagraph:
    _element: object
    _document: object | None = None

    @property
    def text(self) -> str:
        parts: list[str] = []
        for el in self._element.iter():
            if el.tag in (qn("w:t"), qn("w:delText")) and el.text:
                parts.append(el.text)
        return "".join(parts)

    @property
    def style(self):
        pPr = self._element.find(qn("w:pPr"))
        pStyle = pPr.find(qn("w:pStyle")) if pPr is not None else None
        style_id = pStyle.get(qn("w:val"), "") if pStyle is not None else ""
        if self._document is not None:
            name = self._document.style_name_from_id(style_id)
        else:
            name = style_id or "Normal"
        return OpenXmlStyle(name or "Normal")




@dataclass
class OpenXmlStyle:
    name: str


@dataclass
class OpenXmlCell:
    _tc: object
    _document: object

    @property
    def paragraphs(self) -> list[OpenXmlParagraph]:
        return [OpenXmlParagraph(el) for el in list(self._tc) if el.tag == qn("w:p")]


@dataclass
class OpenXmlRow:
    _tr: object
    _document: object

    @property
    def cells(self) -> list[OpenXmlCell]:
        return [OpenXmlCell(el, self._document) for el in list(self._tr) if el.tag == qn("w:tc")]


@dataclass
class OpenXmlTable:
    _tbl: object
    _document: object

    @property
    def rows(self) -> list[OpenXmlRow]:
        return [OpenXmlRow(el, self._document) for el in list(self._tbl) if el.tag == qn("w:tr")]

    def cell(self, row_idx: int, col_idx: int) -> OpenXmlCell:
        return self.rows[row_idx].cells[col_idx]


@dataclass
class OpenXmlRelationship:
    rId: str
    reltype: str
    target_part: object | None
    target_ref: str | None = None


class OpenXmlPart:
    def __init__(self, package: 'OpenXmlDocument', member_path: str):
        self._package = package
        self.member_path = package._normalize_member_path(member_path)
        self.partname = '/' + self.member_path
        self._element = package._get_xml_root(self.member_path)
        self._rels_cache: dict[str, OpenXmlRelationship] | None = None

    @property
    def rels(self) -> dict[str, OpenXmlRelationship]:
        if self._rels_cache is None:
            self._rels_cache = self._package._load_relationships_for_part(self.member_path)
        return self._rels_cache


class OpenXmlDocument:
    def __init__(self, path: str):
        self.path = str(path)
        self._member_bytes: dict[str, bytes] = {}
        self._member_order: list[str] = []
        self._xml_roots: dict[str, object] = {}
        self._parts: dict[str, OpenXmlPart] = {}
        self._style_name_cache: dict[str, str] | None = None

        with zipfile.ZipFile(self.path, 'r') as zf:
            for info in zf.infolist():
                self._member_order.append(info.filename)
                self._member_bytes[info.filename] = zf.read(info.filename)

        self.part = self._get_part('word/document.xml')
        body = self.part._element.find(qn('w:body'))
        if body is None:
            raise ValueError('word/document.xml has no w:body')
        self.element = SimpleNamespace(body=body)

    def _normalize_member_path(self, member_path: str) -> str:
        path = (member_path or '').replace('\\', '/').lstrip('/')
        if not path:
            raise ValueError('empty OPC member path')
        return posixpath.normpath(path)

    def _get_xml_root(self, member_path: str):
        member_path = self._normalize_member_path(member_path)
        if member_path not in self._xml_roots:
            raw = self._member_bytes.get(member_path)
            if raw is None:
                raise KeyError(f'OPC part not found: {member_path}')
            self._xml_roots[member_path] = etree.fromstring(raw)
        return self._xml_roots[member_path]

    def _get_part(self, member_path: str) -> OpenXmlPart:
        member_path = self._normalize_member_path(member_path)
        part = self._parts.get(member_path)
        if part is None:
            part = OpenXmlPart(self, member_path)
            self._parts[member_path] = part
        return part

    def _resolve_target_path(self, source_member: str, target: str) -> str:
        source_member = self._normalize_member_path(source_member)
        target = (target or '').strip()
        if not target:
            raise ValueError('empty relationship target')
        if target.startswith('/'):
            return self._normalize_member_path(target)
        base_dir = posixpath.dirname(source_member)
        return self._normalize_member_path(posixpath.join(base_dir, target))

    def _rels_member_for_part(self, member_path: str) -> str:
        member_path = self._normalize_member_path(member_path)
        folder = posixpath.dirname(member_path)
        filename = posixpath.basename(member_path)
        if folder:
            return f'{folder}/_rels/{filename}.rels'
        return f'_rels/{filename}.rels'

    def _load_relationships_for_part(self, member_path: str) -> dict[str, OpenXmlRelationship]:
        rels_member = self._rels_member_for_part(member_path)
        raw = self._member_bytes.get(rels_member)
        if raw is None:
            return {}

        root = etree.fromstring(raw)
        rels: dict[str, OpenXmlRelationship] = {}
        for rel_el in root.findall(f'{{{PKG_REL_NS}}}Relationship'):
            rid = rel_el.get('Id', '')
            reltype = rel_el.get('Type', '')
            target = rel_el.get('Target')
            target_mode = rel_el.get('TargetMode', '')
            target_part = None
            if target and target_mode.lower() != 'external':
                try:
                    target_path = self._resolve_target_path(member_path, target)
                    if target_path in self._member_bytes and target_path.endswith('.xml'):
                        target_part = self._get_part(target_path)
                except Exception:
                    target_part = None
            rels[rid] = OpenXmlRelationship(
                rId=rid,
                reltype=reltype,
                target_part=target_part,
                target_ref=target,
            )
        return rels

    def _build_style_name_cache(self) -> dict[str, str]:
        if self._style_name_cache is not None:
            return self._style_name_cache
        mapping: dict[str, str] = {}
        for rel in self.part.rels.values():
            reltype = getattr(rel, "reltype", "") or ""
            if reltype.endswith("/styles") and getattr(rel, "target_part", None) is not None:
                root = rel.target_part._element
                for style_el in root.findall(qn("w:style")):
                    sid = style_el.get(qn("w:styleId"), "")
                    name_el = style_el.find(qn("w:name"))
                    name = name_el.get(qn("w:val"), sid) if name_el is not None else sid
                    if sid:
                        mapping[sid] = name
                break
        self._style_name_cache = mapping
        return mapping

    def style_name_from_id(self, style_id: str) -> str:
        if not style_id:
            return "Normal"
        return self._build_style_name_cache().get(style_id, style_id)

    @property
    def paragraphs(self) -> list[OpenXmlParagraph]:
        body = self.element.body
        return [OpenXmlParagraph(el, self) for el in list(body) if el.tag == qn('w:p')]

    @property
    def tables(self) -> list[OpenXmlTable]:
        body = self.element.body
        return [OpenXmlTable(el, self) for el in list(body) if el.tag == qn('w:tbl')]

    def save(self, path):
        buffer = path if hasattr(path, 'write') else None
        if buffer is None:
            out_path = Path(path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            zf_target = out_path
        else:
            zf_target = buffer
        with zipfile.ZipFile(zf_target, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for member in self._member_order:
                if member in self._xml_roots:
                    data = etree.tostring(
                        self._xml_roots[member],
                        xml_declaration=True,
                        encoding='UTF-8',
                        standalone='yes',
                    )
                else:
                    data = self._member_bytes[member]
                zf.writestr(member, data)
