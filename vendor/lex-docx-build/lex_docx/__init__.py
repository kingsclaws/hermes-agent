"""
lex_docx — DOCX 自动化工具库（中英文合同 / 尽调报告通用）

模块：
  edit_ops       — 基础编辑（insert / replace / delete），支持 --tc Track Changes
  api            — 顶层 Python 库接口
  doc_create     — 从零创建 OPC 骨架文档
  doc_scaffold   — 模板克隆 + 主体替换
  new_table_ops  — 插入新表格（grid / kv / merged / nested / diagonal）
  doc_stats      — 快速文档摘要
  toc_ops        — 目录生成/刷新

与 Adeu 的关系：
  Adeu 负责文本级 Track Changes（read / apply edits / accept）；
  lex_docx 负责文档生成、格式控制、表格操作和 Note 注入。

典型 Python API 用法：
  from lex_docx.api import insert, replace, delete, create, new_table

  # 创建文档
  create("out.docx", title="文档标题", font_size=11.5)

  # 直接编辑
  insert("out.docx", para=1, text="新增文字")
  replace("out.docx", para=1, old="旧文", new="新文")

  # Track Changes 模式
  insert("out.docx", para=1, text="新增", tc=True)
  delete("out.docx", para=1, tc=True)
"""

from . import format_brush     # noqa: F401
from . import jt_note          # noqa: F401
from . import lint             # noqa: F401
from . import table_ops        # noqa: F401
from . import defined_terms    # noqa: F401
from . import tc_utils         # noqa: F401
from . import constants        # noqa: F401
from . import config           # noqa: F401
from . import cleanup          # noqa: F401
from . import inject_engine    # noqa: F401
from . import footer_ops       # noqa: F401
from . import numbering_ops    # noqa: F401
from . import tc_ops           # noqa: F401
from . import openxml_core     # noqa: F401
from . import openxml_package  # noqa: F401
from . import doc_create       # noqa: F401
from . import doc_scaffold     # noqa: F401
from . import new_table_ops    # noqa: F401
from . import doc_stats        # noqa: F401
from . import toc_ops          # noqa: F401
from . import edit_ops         # noqa: F401
from . import api              # noqa: F401

from .config import DocConfig, PRESET_JT   # noqa: F401

__version__ = "0.6.0"
__all__ = [
    "doc_create", "doc_scaffold", "new_table_ops",
    "doc_stats", "toc_ops", "edit_ops", "api",
    "format_brush", "jt_note", "lint", "table_ops",
    "defined_terms", "tc_utils", "tc_ops",
    "openxml_core", "openxml_package",
    "constants", "config", "cleanup",
    "inject_engine", "footer_ops", "numbering_ops",
    "DocConfig", "PRESET_JT",
]
