#!/usr/bin/env python3
"""
lex_docx MCP Server — 使 lex_docx 工具可以通过 MCP 协议调用。

用法：
    # 注册到 MCP 配置（stdio 模式）
    # 在 ~/.hermes/config.yaml 或 mcporter 配置中添加：
    #   servers:
    #     lex-docx:
    #       command: python3 /path/to/lex_docx_mcp.py
    
    Agent 可通过 mcporter 或 MCP client 调用：
    mcporter call lex-docx.insert docx=/tmp/doc.docx para=5 text="新增"

暴露的工具：
    - lex_docx_insert     在段落末尾插入文本
    - lex_docx_replace    替换段内文字
    - lex_docx_delete     删除段内文字
    - lex_docx_create     从零创建标准 OPC 骨架文档
    - lex_docx_new_table  在文档中插入新表格
    - lex_docx_stats      快速文档摘要
    - lex_docx_toc        目录生成/刷新
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# 添加 lex_docx 所在目录到 sys.path
LEX_DOCX_DIR = Path(__file__).resolve().parent
if str(LEX_DOCX_DIR) not in sys.path:
    sys.path.insert(0, str(LEX_DOCX_DIR))


# ── MCP 工具注册表 ───────────────────────────────────────────────────────────

def _mcp_tool(name: str, description: str, input_schema: dict):
    """装饰器：注册一个 MCP 工具。"""
    def decorator(func):
        func._mcp = {"name": name, "description": description, "input_schema": input_schema}
        return func
    return decorator


def _import_api(name: str):
    """延迟导入（通过 edit_ops 等底层模块，绕过 api.py 的相对导入）。"""
    if name == "insert":
        import edit_ops
        return edit_ops.insert_text
    if name == "replace":
        import edit_ops
        return edit_ops.replace_text
    if name == "delete":
        import edit_ops
        return edit_ops.delete_text
    if name == "create":
        import doc_create
        return doc_create.create_document
    if name == "new_table":
        import new_table_ops
        return new_table_ops.insert_table
    if name == "stats":
        import doc_stats
        return doc_stats.doc_stats
    if name == "toc_generate":
        import toc_ops
        return toc_ops.toc_generate
    if name == "toc_refresh":
        import toc_ops
        return toc_ops.toc_refresh
    raise ImportError(f"Unknown API name: {name}")


@_mcp_tool(
    "lex_docx_insert",
    "在段落末尾插入文本。--tc 启用 Track Changes。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
            "para": {"type": "integer", "description": "段落索引（从0起）"},
            "text": {"type": "string", "description": "要插入的文本"},
            "tc": {"type": "boolean", "description": "启用 Track Changes 模式"},
            "author": {"type": "string", "description": "修订作者名称"},
            "output": {"type": "string", "description": "输出路径（默认就地覆盖）"},
        },
        "required": ["docx", "para", "text"],
    },
)
def lex_docx_insert(docx: str, para: int, text: str, tc: bool = False,
                    author: str = "agent", output: str | None = None) -> dict:
    _insert = _import_api("insert")
    result = _insert(docx, para, text, tc=tc, author=author, output=output)
    return result.__dict__


@_mcp_tool(
    "lex_docx_replace",
    "替换段内文字。--tc 启用 Track Changes。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
            "para": {"type": "integer", "description": "段落索引（从0起）"},
            "old": {"type": "string", "description": "旧文本"},
            "new": {"type": "string", "description": "新文本"},
            "tc": {"type": "boolean", "description": "启用 Track Changes 模式"},
            "author": {"type": "string", "description": "修订作者名称"},
            "output": {"type": "string", "description": "输出路径（默认就地覆盖）"},
        },
        "required": ["docx", "para", "old", "new"],
    },
)
def lex_docx_replace(docx: str, para: int, old: str, new: str, tc: bool = False,
                     author: str = "agent", output: str | None = None) -> dict:
    _replace = _import_api("replace")
    result = _replace(docx, para, old, new, tc=tc, author=author, output=output)
    return result.__dict__


@_mcp_tool(
    "lex_docx_delete",
    "删除段内文字。--tc 启用 Track Changes。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
            "para": {"type": "integer", "description": "段落索引（从0起）"},
            "text": {"type": "string", "description": "要删除的文字（不指定则删除整段）"},
            "tc": {"type": "boolean", "description": "启用 Track Changes 模式"},
            "author": {"type": "string", "description": "修订作者名称"},
            "output": {"type": "string", "description": "输出路径（默认就地覆盖）"},
        },
        "required": ["docx", "para"],
    },
)
def lex_docx_delete(docx: str, para: int, text: str | None = None,
                    tc: bool = False, author: str = "agent",
                    output: str | None = None) -> dict:
    _delete = _import_api("delete")
    result = _delete(docx, para, text=text, tc=tc, author=author, output=output)
    return result.__dict__


@_mcp_tool(
    "lex_docx_create",
    "从零创建标准 OPC 骨架 .docx 文档。",
    {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "输出文件路径"},
            "title": {"type": "string", "description": "文档标题"},
            "meta": {"type": "string", "description": "元数据（案号/日期等）"},
            "font_size": {"type": "number", "description": "正文字号 pt"},
        },
        "required": ["output"],
    },
)
def lex_docx_create(output: str, title: str = "", meta: str = "",
                    font_size: float = 11.5) -> dict:
    _create = _import_api("create")
    return _create(output, title=title, meta=meta, font_size=font_size)


@_mcp_tool(
    "lex_docx_new_table",
    "在文档中插入新表格（grid / kv / merged / nested / diagonal）。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
            "type": {"type": "string", "description": "表格类型: grid/kv/merged/nested/diagonal"},
            "cols": {"type": "integer", "description": "列数"},
            "rows": {"type": "integer", "description": "数据行数（不含表头）"},
            "headers": {"type": "array", "items": {"type": "string"}, "description": "表头文本列表"},
            "at": {"type": "integer", "description": "插入到指定段落索引之后"},
        },
        "required": ["docx"],
    },
)
def lex_docx_new_table(docx: str, type: str = "grid", cols: int = 2,
                       rows: int = 3, headers: list[str] | None = None,
                       at: int | None = None) -> dict:
    _new_table = _import_api("new_table")
    return _new_table(docx, type=type, cols=cols, rows=rows,
                      headers=headers, at=at)


@_mcp_tool(
    "lex_docx_stats",
    "快速文档摘要（段落数/表格数/字体分布/TCC 数）。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
        },
        "required": ["docx"],
    },
)
def lex_docx_stats(docx: str) -> dict:
    _stats = _import_api("stats")
    result = _stats(docx)
    if "font_distribution" in result:
        result["fonts_top5"] = dict(list(result["font_distribution"].items())[:5])
        del result["font_distribution"]
    return result


@_mcp_tool(
    "lex_docx_toc",
    "目录生成或刷新。",
    {
        "type": "object",
        "properties": {
            "docx": {"type": "string", "description": "目标文档路径"},
            "action": {"type": "string", "description": "generate 或 refresh"},
            "levels": {"type": "string", "description": "级别范围如 1-3"},
        },
        "required": ["docx", "action"],
    },
)
def lex_docx_toc(docx: str, action: str = "generate",
                 levels: str = "1-3") -> dict:
    _gen = _import_api("toc_generate")
    _ref = _import_api("toc_refresh")
    if action == "refresh":
        return _ref(docx)
    return _gen(docx, level_from=int(levels.split("-")[0]),
                level_to=int(levels.split("-")[-1]))


# ── 工具注册表 ───────────────────────────────────────────────────────────────

_MCP_TOOLS: list[dict] = []
_HANDLERS: dict[str, callable] = {}
for name in dir():
    obj = globals()[name]
    mcp_meta = getattr(obj, "_mcp", None)
    if mcp_meta:
        _MCP_TOOLS.append({
            "name": mcp_meta["name"],
            "description": mcp_meta["description"],
            "inputSchema": mcp_meta["input_schema"],
        })
        _HANDLERS[mcp_meta["name"]] = obj


# ── MCP JSON-RPC 处理 ─────────────────────────────────────────────────────────

def _jsonrpc_send(msg: dict) -> None:
    """发送 JSON-RPC 消息到 stdout（MCP 协议要求）。"""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _jsonrpc_error(id: str | int | None, code: int, message: str,
                   data=None) -> None:
    _jsonrpc_send({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message, "data": data},
    })


def _handle_request(msg: dict) -> None:
    request_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if method == "initialize":
        _jsonrpc_send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "lex_docx_mcp",
                    "version": "0.6.0",
                },
            },
        })
    elif method == "notifications/initialized":
        pass  # 无需响应
    elif method == "tools/list":
        _jsonrpc_send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": _MCP_TOOLS},
        })
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        handler = _HANDLERS.get(tool_name)
        if handler is None:
            _jsonrpc_error(request_id, -32601, f"Tool not found: {tool_name}")
            return
        try:
            result = handler(**arguments)
            _jsonrpc_send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)},
                    ],
                },
            })
        except Exception as e:
            tb = traceback.format_exc()
            _jsonrpc_error(request_id, -32603, str(e), data={"traceback": tb})
    else:
        _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


def main():
    """主循环：从 stdin 读取 JSON-RPC 消息并处理。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _handle_request(msg)


if __name__ == "__main__":
    main()
