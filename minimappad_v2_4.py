import ast
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ============================
# Core: map extraction (Python AST)
# ============================

@dataclass
class FuncInfo:
    name: str
    args: List[str]
    returns: Optional[str]
    decorators: List[str]
    lineno: int
    calls: List[str]
    is_async: bool = False          # v2.4 NEW

@dataclass
class ClassInfo:
    name: str
    bases: List[str]
    methods: List[FuncInfo]
    lineno: int

def _unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"

def _fmt_args(fn) -> List[str]:
    args = []
    for a in fn.args.posonlyargs:
        args.append(a.arg)
    for a in fn.args.args:
        args.append(a.arg)
    if fn.args.vararg:
        args.append("*" + fn.args.vararg.arg)
    for a in fn.args.kwonlyargs:
        args.append(a.arg)
    if fn.args.kwarg:
        args.append("**" + fn.args.kwarg.arg)
    return args

def _decorators(fn) -> List[str]:
    return [_unparse(d) for d in fn.decorator_list]

class _CallCollector(ast.NodeVisitor):
    def __init__(self):
        self.calls: List[str] = []

    def visit_Call(self, node: ast.Call):
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name:
            self.calls.append(name)
        self.generic_visit(node)

def _collect_calls(node: ast.AST, top_fn_names: set) -> List[str]:
    v = _CallCollector()
    v.visit(node)
    seen = set()
    out = []
    for c in v.calls:
        if c in top_fn_names and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:12]

def _find_todo_lines(source: str, limit: int = 12) -> List[str]:
    tags = ("TODO", "FIXME", "HACK", "TEMP")
    out = []
    for i, line in enumerate(source.splitlines(), start=1):
        raw = line.rstrip("\n")
        up = raw.upper()
        if not any(t in up for t in tags):
            continue
        stripped = raw.lstrip()
        is_commentish = (
            stripped.startswith("#") or
            stripped.startswith("//") or
            " #" in raw or " //" in raw or
            "/*" in raw or "*/" in raw
        )
        if not is_commentish:
            continue
        s = stripped
        if len(s) > 160:
            s = s[:160] + "..."
        out.append(f"L{i}: {s}")
        if len(out) >= limit:
            break
    return out

def extract_map_python_ast(source: str):
    tree = ast.parse(source)

    imports: List[str] = []
    constants: List[str] = []
    globals_lite: List[str] = []
    functions: List[FuncInfo] = []
    classes: List[ClassInfo] = []

    top_fn_names = set()
    for node in tree.body:
        # v2.4: AsyncFunctionDef도 포함
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_fn_names.add(node.name)

    def build_funcinfo(fn) -> FuncInfo:
        returns = _unparse(fn.returns) if fn.returns else None
        calls = _collect_calls(fn, top_fn_names)
        return FuncInfo(
            name=fn.name,
            args=_fmt_args(fn),
            returns=returns,
            decorators=_decorators(fn),
            lineno=fn.lineno,
            calls=calls,
            is_async=isinstance(fn, ast.AsyncFunctionDef)   # v2.4 NEW
        )

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(_unparse(node))

        elif isinstance(node, ast.Assign):
            targets = []
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    targets.append(t.id)
            if targets:
                constants.append(", ".join(targets))
            else:
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    # v2.4: _private 글로벌 제외
                    if name.startswith("_"):
                        continue
                    if isinstance(node.value, ast.Constant):
                        val = node.value.value
                        if isinstance(val, (str, int, float, bool)) and len(str(val)) <= 120:
                            globals_lite.append(f"{name} = {val!r}")

        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                constants.append(node.target.id)

        # v2.4: FunctionDef + AsyncFunctionDef 통합 처리
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(build_funcinfo(node))

        elif isinstance(node, ast.ClassDef):
            bases = [_unparse(b) for b in node.bases] if node.bases else []
            # v2.4: dataclass 감지
            deco_names = [_unparse(d) for d in node.decorator_list]
            methods: List[FuncInfo] = []
            for item in node.body:
                # v2.4: 클래스 내 async 메서드도 처리
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(build_funcinfo(item))
            classes.append(ClassInfo(
                name=node.name,
                bases=bases,
                methods=methods,
                lineno=node.lineno
            ))

    return imports, constants, globals_lite, functions, classes


def render_map_python(filename: str, imports, constants, globals_lite, functions, classes, todo_lines: List[str]) -> str:
    lines: List[str] = []
    lines.append("### CODE MAP (READ-ONLY) ###")
    lines.append(f"File: {filename or '-'}")
    lines.append("Rule: This is a structure map. Do NOT rewrite code.")
    lines.append("Rule: Ask for a specific function block when needed.")
    lines.append("")

    if todo_lines:
        lines.append("## Warnings (TODO/FIXME/HACK/TEMP)")
        for t in todo_lines:
            lines.append(f"- {t}")
        lines.append("")

    if imports:
        lines.append("## Imports (top-level)")
        for s in imports[:60]:
            lines.append(f"- {s}")
        if len(imports) > 60:
            lines.append(f"- ... (+{len(imports)-60} more)")
        lines.append("")

    if constants:
        lines.append("## Constants (UPPER_CASE)")
        for c in constants[:100]:
            lines.append(f"- {c}")
        lines.append("")

    if globals_lite:
        lines.append("## Globals (lite)")
        for g in globals_lite[:120]:
            lines.append(f"- {g}")
        lines.append("")

    if classes:
        lines.append("## Classes")
        for c in classes:
            base = f"({', '.join(c.bases)})" if c.bases else ""
            lines.append(f"- class {c.name}{base}  [L{c.lineno}]")
            for m in c.methods:
                ret = f" -> {m.returns}" if m.returns and m.returns != "?" else ""
                deco = f" @{', '.join(m.decorators)}" if m.decorators else ""
                calls = f"  calls: {', '.join(m.calls)}" if m.calls else ""
                # v2.4: async 표시
                async_tag = " [async]" if m.is_async else ""
                # v2.4: private 메서드는 흐리게 표시 (괄호로 구분)
                priv = " (private)" if m.name.startswith("_") and not m.name.startswith("__") else ""
                lines.append(f"    - def {m.name}({', '.join(m.args)}){ret}{async_tag}{priv}  [L{m.lineno}]{deco}{calls}")
        lines.append("")

    if functions:
        lines.append("## Functions (top-level)")

        def key_fn(f: FuncInfo):
            name = f.name.lower()
            pri = 9
            if name == "main": pri = 0
            elif name.startswith(("run_", "entry_", "cli_")): pri = 1
            return (pri, f.lineno)

        for f in sorted(functions, key=key_fn):
            ret = f" -> {f.returns}" if f.returns and f.returns != "?" else ""
            deco = f" @{', '.join(f.decorators)}" if f.decorators else ""
            calls = f"  calls: {', '.join(f.calls)}" if f.calls else ""
            # v2.4: async 표시 / private 표시
            async_tag = " [async]" if f.is_async else ""
            priv = " (private)" if f.name.startswith("_") else ""
            lines.append(f"- def {f.name}({', '.join(f.args)}){ret}{async_tag}{priv}  [L{f.lineno}]{deco}{calls}")

        lines.append("")

    return "\n".join(lines)

# ============================
# Lite scanners — PHP (v2.4 improved)
# ============================

def _strip_strings_and_comments_loose(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//.*?$", " ", src, flags=re.M)
    src = re.sub(r"#.*?$", " ", src, flags=re.M)
    src = re.sub(r"(?s)'(?:\\.|[^'\\])*'", "''", src)
    src = re.sub(r'(?s)"(?:\\.|[^"\\])*"', '""', src)
    src = re.sub(r"(?s)`(?:\\.|[^`\\])*`", "``", src)
    return src

def extract_map_php_lite(source: str):
    src = _strip_strings_and_comments_loose(source)
    lines_raw = source.splitlines()   # 원본 (const/define/use 감지용)
    lines = src.splitlines()          # stripped (구조 파싱용)

    namespace = ""
    uses: List[str] = []
    consts: List[str] = []
    defines: List[str] = []
    global_functions: List[Tuple[str, str, str, int]] = []  # (name, args, ret, line)
    classes = []   # list of dict
    calls_set: set = set()

    # ── namespace ────────────────────────────────────────────────
    for i, line in enumerate(lines, start=1):
        m = re.search(r"^\s*namespace\s+([^;{]+)\s*;", line)
        if m:
            namespace = m.group(1).strip()
            break

    # ── use (top-level only: 중괄호 depth 0) ────────────────────
    # v2.4: 클래스 내부 "use TraitName;" 과 분리
    depth = 0
    for i, line in enumerate(lines, start=1):
        depth += line.count("{") - line.count("}")
        if depth == 0:
            m = re.search(r"^\s*use\s+([^;{]+)\s*;", line)
            if m:
                val = m.group(1).strip()
                if re.search(r"\\", val):   # 네임스페이스 구분자 있는 것만
                    uses.append(val)

    # ── const / define: 원본(lines_raw) 기준 ────────────────────
    # v2.4 fix: _strip()이 문자열 내용을 ''로 치환하므로 define("KEY",...) 감지 실패 방지
    for i, line in enumerate(lines_raw, start=1):
        m1 = re.search(r"^\s*const\s+([A-Z0-9_]+)\s*=", line)
        if m1:
            consts.append(f"{m1.group(1)}  [L{i}]")
        m2 = re.search(r"\bdefine\s*\(\s*['\"]([A-Z0-9_]+)['\"]", line, re.I)
        if m2:
            defines.append(f"{m2.group(1)}  [L{i}]")

    # ── class / interface / trait 블록 추적 (depth 기반) ─────────
    # v2.4: depth를 직접 추적해서 글로벌 함수 오귀속 방지
    class_re = re.compile(
        r"^\s*(abstract\s+|final\s+)?(class|interface|trait)\s+([A-Za-z_]\w*)"
        r"(?:\s+extends\s+([A-Za-z_]\w*))?"          # extends
        r"(?:\s+implements\s+([\w,\s\\]+?))?(?:\s*\{|$)",  # implements
        re.I
    )
    method_re = re.compile(
        r"^\s*(public|protected|private)?\s*(static\s+)?(abstract\s+)?function\s+"
        r"([A-Za-z_]\w*)\s*\(([^)]*)\)(?:\s*:\s*([\w\\?|]+))?",
        re.I
    )
    global_fn_re = re.compile(
        r"^function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)(?:\s*:\s*([\w\\?|]+))?",
        re.I
    )

    current_class = None
    class_depth = 0      # 현재 클래스 블록 시작 depth
    brace_depth = 0      # 전체 중괄호 depth

    for i, line in enumerate(lines, start=1):
        open_b = line.count("{")
        close_b = line.count("}")

        # 클래스 선언 감지
        cm = class_re.search(line)
        if cm:
            if current_class:
                classes.append(current_class)
            ext = cm.group(4) or ""
            impl_raw = cm.group(5) or ""
            impl = [x.strip() for x in re.split(r"[,\s]+", impl_raw) if x.strip()] if impl_raw else []
            current_class = {
                "kind": cm.group(2).lower(),
                "name": cm.group(3),
                "extends": ext,
                "implements": impl,
                "line": i,
                "methods": []
            }
            class_depth = brace_depth  # 이 depth에서 클래스 시작
            brace_depth += open_b - close_b
            continue

        brace_depth += open_b - close_b

        # 클래스 블록 종료 감지
        if current_class and brace_depth <= class_depth:
            classes.append(current_class)
            current_class = None
            class_depth = 0
            continue

        # 메서드 or 글로벌 함수
        if current_class:
            mm = method_re.search(line)
            if mm:
                vis = (mm.group(1) or "public").lower()[:3]   # pub/pro/pri
                is_static = bool(mm.group(2))
                is_abstract = bool(mm.group(3))
                mname = mm.group(4)
                margs = " ".join(mm.group(5).split())
                mret = mm.group(6) or ""
                current_class["methods"].append({
                    "name": mname,
                    "args": margs,
                    "vis": vis,
                    "static": is_static,
                    "abstract": is_abstract,
                    "ret": mret,
                    "line": i,
                })
        else:
            # depth 0 글로벌 함수
            gm = global_fn_re.search(line.lstrip())
            if gm:
                gname = gm.group(1)
                gargs = " ".join(gm.group(2).split())
                gret = gm.group(3) or ""
                global_functions.append((gname, gargs, gret, i))

    if current_class:
        classes.append(current_class)

    # ── call hints ───────────────────────────────────────────────
    for m in re.finditer(r"->\s*([A-Za-z_]\w*)\s*\(", source):
        calls_set.add(m.group(1))
    for m in re.finditer(r"::\s*([A-Za-z_]\w*)\s*\(", source):
        calls_set.add(m.group(1))
    calls = sorted(list(calls_set))[:18]

    return namespace, uses, consts, defines, global_functions, classes, calls


def render_map_php(filename: str, namespace: str, uses, consts, defines,
                   global_functions, classes, calls, todo_lines: List[str]) -> str:
    lines: List[str] = []
    lines.append("### CODE MAP (READ-ONLY) ###")
    lines.append(f"File: {filename or '-'}")
    lines.append("Mode: PHP-lite (regex/token scan)")
    lines.append("Rule: This is a structure map. Do NOT rewrite code.")
    lines.append("Rule: Ask for a specific function/class block when needed.")
    lines.append("")

    if todo_lines:
        lines.append("## Warnings (TODO/FIXME/HACK/TEMP)")
        for t in todo_lines:
            lines.append(f"- {t}")
        lines.append("")

    if namespace:
        lines.append("## Namespace")
        lines.append(f"- {namespace}")
        lines.append("")

    if uses:
        lines.append("## Use")
        for u in uses[:80]:
            lines.append(f"- {u}")
        if len(uses) > 80:
            lines.append(f"- ... (+{len(uses)-80} more)")
        lines.append("")

    if consts or defines:
        lines.append("## Constants")
        for c in consts[:120]:
            lines.append(f"- const {c}")
        for d in defines[:120]:
            lines.append(f"- define {d}")
        lines.append("")

    if classes:
        lines.append("## Classes / Interfaces / Traits")
        for c in classes:
            # v2.4: extends / implements 표시
            ext_str = f" extends {c['extends']}" if c.get("extends") else ""
            impl_str = ""
            if c.get("implements"):
                impl_str = f" implements {', '.join(c['implements'])}"
            lines.append(f"- {c['kind']} {c['name']}{ext_str}{impl_str}  [L{c['line']}]")

            for m in c["methods"][:60]:
                # v2.4: vis + static + abstract + return type
                tags = [m["vis"]]
                if m["static"]: tags.append("static")
                if m["abstract"]: tags.append("abstract")
                tag_str = "/".join(tags)
                ret_str = f" : {m['ret']}" if m["ret"] else ""
                lines.append(f"    - [{tag_str}] function {m['name']}({m['args']}){ret_str}  [L{m['line']}]")
            if len(c["methods"]) > 60:
                lines.append(f"    - ... (+{len(c['methods'])-60} more)")
        lines.append("")

    # v2.4: 글로벌 functions 별도 섹션 (클래스와 분리)
    if global_functions:
        lines.append("## Global Functions")
        for name, args, ret, ln in global_functions[:200]:
            ret_str = f" : {ret}" if ret else ""
            lines.append(f"- function {name}({args}){ret_str}  [L{ln}]")
        if len(global_functions) > 200:
            lines.append(f"- ... (+{len(global_functions)-200} more)")
        lines.append("")

    if calls:
        lines.append("## Call Hints (-> / ::)")
        lines.append("- " + ", ".join(calls))
        lines.append("")

    return "\n".join(lines)

# ============================
# Kotlin-lite (v2.3 — Compose-aware, unchanged)
# ============================

def extract_map_kotlin_lite(source: str):
    lines = source.splitlines()
    pkg = ""
    imports: List[Tuple[str, int]] = []
    decls: List[Tuple[str, int]] = []
    top_funs: List[Tuple[str, int]] = []
    local_funs: List[Tuple[str, int]] = []
    companions: List[int] = []
    state_vars: List[Tuple[str, int]] = []
    effect_blocks: List[Tuple[str, int]] = []
    overlay_guards: List[Tuple[str, int]] = []
    overlay_seen: set = set()  # FIX2: dedup tracker

    def _collect_prev_annotations(idx_1based: int, lookback: int = 6) -> List[str]:
        tags: List[str] = []
        j = idx_1based - 1
        start = max(0, j - lookback)
        k = j - 1
        while k >= start:
            t = lines[k].strip()
            if not t:
                k -= 1
                continue
            if t.startswith("@file:"):
                k -= 1
                continue
            if t.startswith("@"):
                m = re.match(r"^@([A-Za-z_]\w*)", t)
                if m:
                    tags.append("@" + m.group(1))
                k -= 1
                continue
            break
        tags.reverse()
        if len(tags) > 4:
            tags = tags[:4] + ["@..."]
        return tags

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue

        indent = len(raw) - len(raw.lstrip())

        m = re.match(r"^package\s+(.+)$", line)
        if m and not pkg:
            pkg = m.group(1).strip()
            continue

        m = re.match(r"^import\s+(.+)$", line)
        if m:
            imports.append((m.group(1).strip(), i))
            continue

        if "companion object" in line:
            companions.append(i)

        m = re.match(r"^(?:data\s+|sealed\s+|open\s+|abstract\s+)?(class|interface|object|enum\s+class)\s+([A-Za-z_]\w*)", line)
        if m:
            kind = m.group(1).replace("  ", " ").strip()
            name = m.group(2)
            decls.append((f"{kind} {name}", i))
            continue

        m = re.match(r"(?:var|val)\s+(\w+)\s+by\s+remember(?:Saveable)?", line)
        if m:
            state_vars.append((m.group(1), i))
            continue

        m = re.match(r"(LaunchedEffect|DisposableEffect|SideEffect|rememberCoroutineScope)\s*\(", line)
        if m:
            effect_blocks.append((m.group(1), i))

        # FIX1: overlay guard — show*/overlay* 전부, reset* 는 Index/One/All 한정
        # FIX2: 중복 방지 — overlay_seen 은 루프 외부에서 유지되는 set 사용
        if re.match(r"if\s*\(\s*(show\w+|overlay\w+)", line):
            mc = re.match(r"if\s*\(\s*(\w+)", line)
            cond = mc.group(1) if mc else line[:30]
            if cond not in overlay_seen:
                overlay_guards.append((f"if ({cond})", i))
                overlay_seen.add(cond)
        elif re.match(r"if\s*\(\s*(reset\w*(?:Index|One|All)\b)", line):
            mc = re.match(r"if\s*\(\s*(\w+)", line)
            cond = mc.group(1) if mc else line[:30]
            if cond not in overlay_seen:
                overlay_guards.append((f"if ({cond})", i))
                overlay_seen.add(cond)
        elif re.match(r"(\w+)\?\.let\s*\{", line):
            mc = re.match(r"(\w+)\?\.let", line)
            cond = mc.group(1) if mc else line[:30]
            if cond not in overlay_seen:
                overlay_guards.append((f"{cond}?.let {{ }}", i))
                overlay_seen.add(cond)

        m = re.match(
            r"^(?:(?:public|private|protected|internal)\s+)?"
            r"(?:(?:final|open|abstract)\s+)?"
            r"(?:(?:override|suspend|inline|tailrec|operator|infix|external)\s+)*"
            r"fun\s+([A-Za-z_]\w*)\s*\((.*)\)",
            line
        )
        if m:
            name = m.group(1)
            args = " ".join(m.group(2).split())
            ann = _collect_prev_annotations(i, lookback=6)
            ann_tag = f" [{' '.join(ann)}]" if ann else ""
            sig = f"fun {name}({args}){ann_tag}"
            # FIX3: @Composable 어노테이션이 있으면 indent 무관하게 top_funs
            if "@Composable" in ann_tag or indent < 4:
                top_funs.append((sig, i))
            else:
                local_funs.append((sig, i))
            continue

    return pkg, imports, decls, top_funs, local_funs, companions, state_vars, effect_blocks, overlay_guards


def render_map_kotlin(filename: str, pkg: str, imports, decls, top_funs, local_funs,
                      companions, state_vars, effect_blocks, overlay_guards,
                      todo_lines: List[str]) -> str:
    out: List[str] = []
    out.append("### CODE MAP (READ-ONLY) ###")
    out.append(f"File: {filename or '-'}")
    out.append("Mode: Kotlin-lite (Compose-aware)")
    out.append("Rule: This is a structure map. Do NOT rewrite code.")
    out.append("Rule: Ask for a specific function/class block when needed.")
    out.append("")

    if todo_lines:
        out.append("## Warnings (TODO/FIXME/HACK/TEMP)")
        for t in todo_lines:
            out.append(f"- {t}")
        out.append("")

    if pkg:
        out.append("## Package")
        out.append(f"- {pkg}")
        out.append("")

    if imports:
        out.append("## Imports")
        for imp, ln in imports[:120]:
            out.append(f"- {imp}  [L{ln}]")
        if len(imports) > 120:
            out.append(f"- ... (+{len(imports)-120} more)")
        out.append("")

    if decls:
        out.append("## Declarations")
        for d, ln in decls[:160]:
            out.append(f"- {d}  [L{ln}]")
        out.append("")

    if companions:
        out.append("## Companion Object")
        for ln in companions[:40]:
            out.append(f"- companion object  [L{ln}]")
        out.append("")

    if top_funs:
        out.append("## Top-level Functions")
        for sig, ln in top_funs[:220]:
            out.append(f"- {sig}  [L{ln}]")
        out.append("")

    if state_vars:
        out.append("## State Vars (remember / rememberSaveable)")
        for name, ln in state_vars[:80]:
            out.append(f"- var {name}  [L{ln}]")
        if len(state_vars) > 80:
            out.append(f"- ... (+{len(state_vars)-80} more)")
        out.append("")

    if effect_blocks:
        out.append("## Effect / Scope Blocks")
        for kind, ln in effect_blocks[:40]:
            out.append(f"- {kind}(...)  [L{ln}]")
        out.append("")

    if local_funs:
        out.append("## Local Functions (in Composable)")
        for sig, ln in local_funs[:100]:
            out.append(f"- {sig}  [L{ln}]")
        if len(local_funs) > 100:
            out.append(f"- ... (+{len(local_funs)-100} more)")
        out.append("")

    if overlay_guards:
        out.append("## UI Overlay Guards (if-blocks / let-blocks)")
        for cond, ln in overlay_guards[:60]:
            out.append(f"- {cond}  [L{ln}]")
        out.append("")

    return "\n".join(out)

# ============================
# Java-lite
# ============================

def extract_map_java_lite(source: str):
    lines = source.splitlines()
    pkg = ""
    imports: List[Tuple[str, int]] = []
    decls: List[Tuple[str, int]] = []
    methods: List[Tuple[str, int]] = []

    decl_re = re.compile(r"^(?:public\s+)?(?:abstract\s+|final\s+)?(class|interface|enum|record)\s+([A-Za-z_]\w*)")
    meth_re = re.compile(r"^(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?([A-Za-z_][\w<>\[\]]*)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)")

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue

        m = re.match(r"^package\s+([^;]+)\s*;", line)
        if m and not pkg:
            pkg = m.group(1).strip()
            continue

        m = re.match(r"^import\s+([^;]+)\s*;", line)
        if m:
            imports.append((m.group(1).strip(), i))
            continue

        m = decl_re.match(line)
        if m:
            decls.append((f"{m.group(1)} {m.group(2)}", i))
            continue

        if line.startswith(("if", "for", "while", "switch", "return", "throw", "new ")):
            continue

        m = meth_re.match(line)
        if m:
            ret = m.group(1)
            name = m.group(2)
            args = " ".join(m.group(3).split())
            methods.append((f"{name}({args}) : {ret}", i))

    return pkg, imports, decls, methods


def render_map_java(filename: str, pkg: str, imports, decls, methods, todo_lines: List[str]) -> str:
    out: List[str] = []
    out.append("### CODE MAP (READ-ONLY) ###")
    out.append(f"File: {filename or '-'}")
    out.append("Mode: Java-lite (line scan)")
    out.append("Rule: This is a structure map. Do NOT rewrite code.")
    out.append("Rule: Ask for a specific method/class block when needed.")
    out.append("")

    if todo_lines:
        out.append("## Warnings (TODO/FIXME/HACK/TEMP)")
        for t in todo_lines:
            out.append(f"- {t}")
        out.append("")

    if pkg:
        out.append("## Package")
        out.append(f"- {pkg}")
        out.append("")

    if imports:
        out.append("## Imports")
        for imp, ln in imports[:120]:
            out.append(f"- {imp}  [L{ln}]")
        if len(imports) > 120:
            out.append(f"- ... (+{len(imports)-120} more)")
        out.append("")

    if decls:
        out.append("## Declarations")
        for d, ln in decls[:180]:
            out.append(f"- {d}  [L{ln}]")
        out.append("")

    if methods:
        out.append("## Methods (lite)")
        for sig, ln in methods[:260]:
            out.append(f"- {sig}  [L{ln}]")
        out.append("")

    return "\n".join(out)

# ============================
# Language sniffer
# ============================

def sniff_lite_language(source: str, filename: str = "") -> str:
    fn = (filename or "").lower().strip()
    if fn.endswith(".php"): return "PHP"
    if fn.endswith(".kt") or fn.endswith(".kts"): return "Kotlin-lite"
    if fn.endswith(".java"): return "Java-lite"

    head = "\n".join(source.splitlines()[:120])
    head_l = head.lower()
    s = source

    if "<?php" in head_l: return "PHP"
    if re.search(r"^\s*package\s+[a-zA-Z_][\w.]*\s*$", head, flags=re.M):
        if re.search(r"\b(fun|companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s):
            return "Kotlin-lite"
    if re.search(r"^\s*package\s+[a-zA-Z_][\w.]*\s*;\s*$", head, flags=re.M):
        return "Java-lite"

    score = {"PHP": 0, "Kotlin-lite": 0, "Java-lite": 0}
    score["PHP"] += 4 * len(re.findall(r"\$\w+", s))
    score["PHP"] += 3 * len(re.findall(r"->\s*[A-Za-z_]\w*\s*\(", s))
    score["PHP"] += 3 * len(re.findall(r"::\s*[A-Za-z_]\w*\s*\(", s))
    if re.search(r"^\s*namespace\s+[^;{]+\s*;", s, flags=re.M): score["PHP"] += 8
    if re.search(r"^\s*use\s+[^;]+\s*;", s, flags=re.M): score["PHP"] += 4
    if re.search(r"\bfunction\s+\w+\s*\(", s): score["PHP"] += 2
    if re.search(r"^\s*fun\s+\w+\s*\(", s, flags=re.M): score["Kotlin-lite"] += 10
    # v2.4fix: private/suspend/internal fun 등 수식어가 앞에 붙는 경우 감지
    if re.search(r"\b(private|public|internal|protected|suspend|inline|override)\s+fun\s+\w+\s*\(", s): score["Kotlin-lite"] += 10
    if re.search(r"\boverride\s+fun\s+\w+\s*\(", s): score["Kotlin-lite"] += 8
    if re.search(r"\b(companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s): score["Kotlin-lite"] += 8
    if re.search(r"\bval\s+\w+\s*[:=]|\bvar\s+\w+\s*[:=]", s): score["Kotlin-lite"] += 3
    if re.search(r"\bwhen\s*\(", s): score["Kotlin-lite"] += 2
    if re.search(r"@Composable\b", s): score["Kotlin-lite"] += 6
    # v2.4fix: by remember 는 Kotlin 고유 패턴
    if re.search(r"\bby\s+remember", s): score["Kotlin-lite"] += 8
    if re.search(r"^\s*import\s+[\w.]+\s*;\s*$", s, flags=re.M): score["Java-lite"] += 4
    if re.search(r"\b(public|protected|private)\s+(class|interface|enum|record)\s+\w+", s): score["Java-lite"] += 9
    if re.search(r"\bstatic\b", s): score["Java-lite"] += 2

    best = max(score, key=score.get)
    best_val = score[best]
    if best_val == 0: return "Kotlin-lite"
    sorted_scores = sorted(score.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) >= 2:
        top, second = sorted_scores[0], sorted_scores[1]
        if top[1] - second[1] <= 2:
            if re.search(r"\$\w+", s): return "PHP"
            if re.search(r"\b(fun|companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s): return "Kotlin-lite"
    return best

# ============================
# Redaction
# ============================

_RE_SSN = re.compile(r"\b\d{6}-\d{7}\b")
_RE_PHONE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")
_RE_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_RE_SECRET_ASSIGN = re.compile(
    r"""(?ix)^(\s*)([A-Z0-9_]*(TOKEN|API[_-]?KEY|SECRET|PASSWORD|PASS|AUTH|BEARER)[A-Z0-9_]*)(\s*=\s*)(.+?)\s*$"""
)

def redact_text(s: str) -> str:
    s = _RE_SSN.sub("***REDACTED_SSN***", s)
    s = _RE_PHONE.sub("***REDACTED_PHONE***", s)
    s = _RE_EMAIL.sub("***REDACTED_EMAIL***", s)
    out_lines = []
    for line in s.splitlines():
        m = _RE_SECRET_ASSIGN.match(line)
        if m:
            indent, varname, _, eq, _rhs = m.groups()
            out_lines.append(f"{indent}{varname}{eq}'***REDACTED_SECRET***'")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)

# ============================
# GUI (v2.4)
# ============================

class MiniMapPadV24(tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        self.title("MiniMapPad v2.4 — Code Map (Python async + PHP improved)")
        self.geometry("1260x860")
        self.current_file = ""

        self.var_auto_copy = tk.BooleanVar(value=True)
        self.var_topmost = tk.BooleanVar(value=False)
        self.var_redact = tk.BooleanVar(value=True)
        self.var_todo = tk.BooleanVar(value=True)
        self.var_dark = tk.BooleanVar(value=True)
        self.var_lang = tk.StringVar(value="Auto")
        self.lang_choices = ["Auto", "Python", "PHP", "Kotlin-lite", "Java-lite"]

        self.font_ui = ("Segoe UI Mono", 12)
        self.font_btn = ("Segoe UI Mono", 12, "bold")
        self.font_text = ("Segoe UI Mono", 13)

        self._build_ui()
        self.apply_theme()

    def _build_ui(self):
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(10, 6))

        tk.Button(bar, text="Generate Map", command=self.generate, font=self.font_btn, width=12).pack(side="left")
        tk.Button(bar, text="Copy Output", command=self.copy_result, font=self.font_ui, width=11).pack(side="left", padx=6)
        tk.Button(bar, text="Open File (optional)", command=self.open_file, font=self.font_ui, width=16).pack(side="left")

        opt = tk.Frame(bar)
        opt.pack(side="left", padx=14)
        tk.Checkbutton(opt, text="Auto-copy on Generate", variable=self.var_auto_copy, font=self.font_ui).pack(side="left")
        tk.Checkbutton(opt, text="Redact secrets/PII", variable=self.var_redact, font=self.font_ui).pack(side="left", padx=8)
        tk.Checkbutton(opt, text="TODO warnings", variable=self.var_todo, font=self.font_ui).pack(side="left")
        tk.Checkbutton(opt, text="Always on top", variable=self.var_topmost, command=self.apply_topmost, font=self.font_ui).pack(side="left", padx=8)
        tk.Checkbutton(opt, text="Dark mode", variable=self.var_dark, command=self.apply_theme, font=self.font_ui).pack(side="left", padx=6)
        tk.Label(opt, text="Language:", font=self.font_ui).pack(side="left", padx=(14, 4))
        tk.OptionMenu(opt, self.var_lang, *self.lang_choices).pack(side="left")

        self.lbl_status = tk.Label(
            self,
            text="Paste code → Generate Map → Auto-copy → Paste to LLM (Ctrl+V).  v2.4: Python async + PHP improved",
            font=self.font_ui, anchor="w", justify="left"
        )
        self.lbl_status.pack(fill="x", padx=12, pady=(0, 8))

        pane = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief="raised", bd=0)
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        f_in = tk.Frame(pane)
        tk.Label(f_in, text="Step1 - Paste code here", font=self.font_ui).pack(anchor="w", pady=(0, 6))
        self.txt_in = tk.Text(f_in, height=18, wrap="none", undo=True, font=self.font_text)
        self.txt_in.pack(fill="both", expand=True, pady=(0, 10))
        pane.add(f_in)

        f_out = tk.Frame(pane)
        tk.Label(f_out, text="Step2 - Code Map Output (Auto-copied after Generate)", font=self.font_ui).pack(anchor="w", pady=(6, 6))
        self.txt_out = tk.Text(f_out, height=18, wrap="none", font=self.font_text)
        self.txt_out.pack(fill="both", expand=True)
        pane.add(f_out)

    def apply_topmost(self):
        self.attributes("-topmost", bool(self.var_topmost.get()))

    def apply_theme(self):
        dark = bool(self.var_dark.get())
        if dark:
            bg, fg, sub = "#0f0f0f", "#e6e6e6", "#2a2a2a"
            text_bg, text_fg, insert = "#1E1F23", "#E1E1E1", "#ffffff"
        else:
            bg, fg, sub = "#f5f5f5", "#111111", "#ffffff"
            text_bg, text_fg, insert = "#ffffff", "#111111", "#111111"

        self.configure(bg=bg)
        for w in self.winfo_children():
            self._apply_widget_theme(w, bg, fg, sub, text_bg, text_fg, insert)

        if dark:
            in_bg, out_bg, fg, insert = "#2a2b30", "#1E1F23", "#E1E1E1", "#ffffff"
        else:
            in_bg, out_bg, fg, insert = "#ffffff", "#ffffff", "#111111", "#111111"
        try:
            self.txt_in.configure(bg=in_bg, fg=fg, insertbackground=insert)
            self.txt_out.configure(bg=out_bg, fg=fg, insertbackground=insert)
        except Exception:
            pass

    def _apply_widget_theme(self, w, bg, fg, sub, text_bg, text_fg, insert):
        cls = w.winfo_class()
        if cls in ("Frame", "PanedWindow"):
            try: w.configure(bg=bg)
            except Exception: pass
        if cls == "Label":
            try: w.configure(bg=bg, fg=fg)
            except Exception: pass
        if cls == "Button":
            try: w.configure(bg=sub, fg=fg, activebackground=sub, activeforeground=fg)
            except Exception: pass
        if cls == "Checkbutton":
            try: w.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=bg)
            except Exception: pass
        if cls == "Text":
            try: w.configure(bg=text_bg, fg=text_fg, insertbackground=insert)
            except Exception: pass
        if cls in ("Menubutton",):
            try: w.configure(bg=sub, fg=fg, activebackground=sub, activeforeground=fg)
            except Exception: pass
        for c in w.winfo_children():
            self._apply_widget_theme(c, bg, fg, sub, text_bg, text_fg, insert)

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("Code files", "*.py *.php *.kt *.java *.txt"),
                ("Python files", "*.py"), ("PHP files", "*.php"),
                ("Kotlin files", "*.kt"), ("Java files", "*.java"),
                ("All files", "*.*"),
            ]
        )
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            self.current_file = path
            self.txt_in.delete("1.0", "end")
            self.txt_in.insert("1.0", src)
            self.set_status(f"Opened: {os.path.basename(path)}  → Click 'Generate Map'. (Language: {self.var_lang.get()})")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def set_status(self, msg: str):
        self.lbl_status.config(text=msg)

    def _write_output(self, out: str):
        if self.var_redact.get():
            out = redact_text(out)
        self.txt_out.delete("1.0", "end")
        self.txt_out.insert("1.0", out)
        return out

    def _finalize_copy(self, out: str, mode_label: str):
        if self.var_auto_copy.get():
            self.clipboard_clear()
            self.clipboard_append(out)
            self.title(f"MiniMapPad v2.4 — ✅ Copied ({mode_label})")
            self.set_status(f"✅ Generated + copied. Mode: {mode_label}. Now paste into ChatGPT/Claude (Ctrl+V).")
        else:
            self.title(f"MiniMapPad v2.4 — Generated ({mode_label})")
            self.set_status(f"✅ Generated. Mode: {mode_label}. (Auto-copy is OFF)")

    def _run_kotlin(self, src, fname, todo_lines, label_suffix=""):
        pkg, imports, decls, top_funs, local_funs, companions, state_vars, effect_blocks, overlay_guards = extract_map_kotlin_lite(src)
        out = render_map_kotlin(fname, pkg, imports, decls, top_funs, local_funs, companions, state_vars, effect_blocks, overlay_guards, todo_lines)
        out = self._write_output(out)
        self._finalize_copy(out, f"Kotlin-lite{label_suffix}")

    def generate(self):
        src = self.txt_in.get("1.0", "end").strip()
        if not src:
            messagebox.showwarning("Info", "Input is empty. Paste code first.")
            return

        todo_lines = _find_todo_lines(src, limit=12) if self.var_todo.get() else []
        fname = os.path.basename(self.current_file) if self.current_file else ""
        selected = self.var_lang.get()

        if selected == "Python":
            try:
                imports, constants, globals_lite, functions, classes = extract_map_python_ast(src)
                out = render_map_python(fname, imports, constants, globals_lite, functions, classes, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "Python (AST)")
            except SyntaxError as e:
                messagebox.showerror("Parse failed (SyntaxError)", f"{e.msg}\n(L{e.lineno}:{e.offset})")
            except Exception as e:
                messagebox.showerror("Error", str(e))
            return

        if selected == "PHP":
            try:
                ns, uses, consts, defines, gfuncs, classes, calls = extract_map_php_lite(src)
                out = render_map_php(fname, ns, uses, consts, defines, gfuncs, classes, calls, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "PHP-lite")
            except Exception as e:
                out = "### CODE MAP (READ-ONLY) ###\nMode: PHP-lite\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "PHP-lite (partial)")
            return

        if selected == "Kotlin-lite":
            try:
                self._run_kotlin(src, fname, todo_lines)
            except Exception as e:
                out = "### CODE MAP (READ-ONLY) ###\nMode: Kotlin-lite\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "Kotlin-lite (partial)")
            return

        if selected == "Java-lite":
            try:
                pkg, imports, decls, methods = extract_map_java_lite(src)
                out = render_map_java(fname, pkg, imports, decls, methods, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "Java-lite")
            except Exception as e:
                out = "### CODE MAP (READ-ONLY) ###\nMode: Java-lite\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "Java-lite (partial)")
            return

        # Auto mode
        if selected == "Auto":
            try:
                imports, constants, globals_lite, functions, classes = extract_map_python_ast(src)
                out = render_map_python(fname, imports, constants, globals_lite, functions, classes, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "Auto → Python (AST)")
                return
            except (SyntaxError, Exception):
                pass

            lite = sniff_lite_language(src, fname)
            try:
                if lite == "PHP":
                    ns, uses, consts, defines, gfuncs, classes, calls = extract_map_php_lite(src)
                    out = render_map_php(fname, ns, uses, consts, defines, gfuncs, classes, calls, todo_lines)
                    out = self._write_output(out)
                    self._finalize_copy(out, "Auto → PHP-lite")
                elif lite == "Java-lite":
                    pkg, imports, decls, methods = extract_map_java_lite(src)
                    out = render_map_java(fname, pkg, imports, decls, methods, todo_lines)
                    out = self._write_output(out)
                    self._finalize_copy(out, "Auto → Java-lite")
                else:
                    self._run_kotlin(src, fname, todo_lines, " (Auto)")
            except Exception as e:
                out = "### CODE MAP (READ-ONLY) ###\nMode: Auto (lite fallback)\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "Auto → lite (partial)")
            return

        messagebox.showerror("Error", f"Unknown language mode: {selected}")

    def copy_result(self):
        out = self.txt_out.get("1.0", "end").strip()
        if not out:
            messagebox.showwarning("Info", "No output to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(out)
        self.title("MiniMapPad v2.4 — 📋 Copied")
        self.set_status("📋 Copied output to clipboard.")

if __name__ == "__main__":
    app = MiniMapPadV24()
    app.mainloop()