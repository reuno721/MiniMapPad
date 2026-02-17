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

@dataclass
class ClassInfo:
    name: str
    bases: List[str]
    methods: List[FuncInfo]
    lineno: int

def _unparse(node) -> str:
    try:
        return ast.unparse(node)  # Python 3.9+
    except Exception:
        return "?"

def _fmt_args(fn: ast.FunctionDef) -> List[str]:
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

def _decorators(fn: ast.FunctionDef) -> List[str]:
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
    return out[:12]  # cap

def _find_todo_lines(source: str, limit: int = 12) -> List[str]:
    """
    v2.1: Reduce false positives.
    Only flag TODO-like tags if the line looks like a comment line.
    """
    tags = ("TODO", "FIXME", "HACK", "TEMP")
    out = []

    for i, line in enumerate(source.splitlines(), start=1):
        raw = line.rstrip("\n")
        up = raw.upper()

        if not any(t in up for t in tags):
            continue

        # Only consider comment-ish lines
        stripped = raw.lstrip()
        is_commentish = (
            stripped.startswith("#") or
            stripped.startswith("//") or
            " #" in raw or
            " //" in raw or
            "/*" in raw or
            "*/" in raw
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
        if isinstance(node, ast.FunctionDef):
            top_fn_names.add(node.name)

    def build_funcinfo(fn: ast.FunctionDef) -> FuncInfo:
        returns = _unparse(fn.returns) if fn.returns else None
        calls = _collect_calls(fn, top_fn_names)
        return FuncInfo(
            name=fn.name,
            args=_fmt_args(fn),
            returns=returns,
            decorators=_decorators(fn),
            lineno=fn.lineno,
            calls=calls
        )

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(_unparse(node))

        elif isinstance(node, ast.Assign):
            # UPPER_CASE constants
            targets = []
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    targets.append(t.id)
            if targets:
                constants.append(", ".join(targets))
            else:
                # lightweight globals: NAME = "str"/123/True/False (short)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if isinstance(node.value, ast.Constant):
                        val = node.value.value
                        if isinstance(val, (str, int, float, bool)) and len(str(val)) <= 120:
                            globals_lite.append(f"{name} = {val!r}")

        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                constants.append(node.target.id)

        elif isinstance(node, ast.FunctionDef):
            functions.append(build_funcinfo(node))

        elif isinstance(node, ast.ClassDef):
            bases = [_unparse(b) for b in node.bases] if node.bases else []
            methods: List[FuncInfo] = []
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods.append(build_funcinfo(item))
            classes.append(ClassInfo(name=node.name, bases=bases, methods=methods, lineno=node.lineno))

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
        if len(constants) > 100:
            lines.append(f"- ... (+{len(constants)-100} more)")
        lines.append("")

    if globals_lite:
        lines.append("## Globals (lite)")
        for g in globals_lite[:120]:
            lines.append(f"- {g}")
        if len(globals_lite) > 120:
            lines.append(f"- ... (+{len(globals_lite)-120} more)")
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
                lines.append(f"    - def {m.name}({', '.join(m.args)}){ret}  [L{m.lineno}]{deco}{calls}")
        lines.append("")

    if functions:
        lines.append("## Functions (top-level)")

        def key_fn(f: FuncInfo):
            name = f.name.lower()
            pri = 9
            if name == "main":
                pri = 0
            elif name.startswith(("run_", "entry_", "cli_")):
                pri = 1
            return (pri, f.lineno)

        for f in sorted(functions, key=key_fn):
            ret = f" -> {f.returns}" if f.returns and f.returns != "?" else ""
            deco = f" @{', '.join(f.decorators)}" if f.decorators else ""
            calls = f"  calls: {', '.join(f.calls)}" if f.calls else ""
            lines.append(f"- def {f.name}({', '.join(f.args)}){ret}  [L{f.lineno}]{deco}{calls}")

        lines.append("")

    return "\n".join(lines)

# ============================
# Lite scanners (PHP / Kotlin / Java)
# ============================

def _strip_strings_and_comments_loose(src: str) -> str:
    """
    Roughly remove strings and comments to reduce regex noise.
    Not perfect (by design), but safe for "lite map" purposes.
    """
    # block comments
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    # line comments // and #
    src = re.sub(r"//.*?$", " ", src, flags=re.M)
    src = re.sub(r"#.*?$", " ", src, flags=re.M)
    # strings (single/double/backtick) - loose
    src = re.sub(r"(?s)'(?:\\.|[^'\\])*'", "''", src)
    src = re.sub(r'(?s)"(?:\\.|[^"\\])*"', '""', src)
    src = re.sub(r"(?s)`(?:\\.|[^`\\])*`", "``", src)
    return src

def extract_map_php_lite(source: str):
    src0 = source
    src = _strip_strings_and_comments_loose(source)

    namespace = ""
    uses: List[str] = []
    consts: List[str] = []
    defines: List[str] = []
    functions: List[Tuple[str, str, int]] = []  # (name, args, line)
    classes: List[Tuple[str, str, int, List[Tuple[str, str, int]]]] = []  # (kind,name,line,methods)
    calls: List[str] = []

    lines = src.splitlines()

    # namespace (first match)
    for i, line in enumerate(lines, start=1):
        m = re.search(r"^\s*namespace\s+([^;{]+)\s*;", line)
        if m:
            namespace = m.group(1).strip()
            break

    # use statements (top-ish)
    for i, line in enumerate(lines, start=1):
        m = re.search(r"^\s*use\s+([^;]+)\s*;", line)
        if m:
            uses.append(m.group(1).strip())

    # const / define
    for i, line in enumerate(lines, start=1):
        m1 = re.search(r"^\s*const\s+([A-Z0-9_]+)\s*=", line)
        if m1:
            consts.append(f"{m1.group(1)}  [L{i}]")
        m2 = re.search(r"\bdefine\s*\(\s*['\"]([A-Z0-9_]+)['\"]", line, flags=re.I)
        if m2:
            defines.append(f"{m2.group(1)}  [L{i}]")

    # functions (global)
    fn_re = re.compile(r"^\s*(?:final\s+|abstract\s+)?function\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)", re.I)
    for i, line in enumerate(lines, start=1):
        # avoid method lines inside classes? (still ok; we'll also capture methods separately)
        m = fn_re.search(line)
        if m:
            name = m.group(1)
            args = " ".join(m.group(2).split())
            functions.append((name, args, i))

    # classes / interfaces / traits
    class_re = re.compile(r"^\s*(abstract\s+|final\s+)?(class|interface|trait)\s+([A-Za-z_]\w*)", re.I)
    method_re = re.compile(r"^\s*(?:public|protected|private)?\s*(?:static\s+)?function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", re.I)

    # naive block tracking: find class header then scan until next class header; methods collected by regex
    current = None  # dict
    for i, line in enumerate(lines, start=1):
        cm = class_re.search(line)
        if cm:
            if current:
                classes.append((current["kind"], current["name"], current["line"], current["methods"]))
            current = {
                "kind": cm.group(2).lower(),
                "name": cm.group(3),
                "line": i,
                "methods": []
            }
            continue

        if current:
            mm = method_re.search(line)
            if mm:
                mname = mm.group(1)
                margs = " ".join(mm.group(2).split())
                current["methods"].append((mname, margs, i))

    if current:
        classes.append((current["kind"], current["name"], current["line"], current["methods"]))

    # call hints: ->foo( , ::bar(
    # use original (not stripped) to catch more realistically, but still cap.
    call_set = set()
    for m in re.finditer(r"->\s*([A-Za-z_]\w*)\s*\(", src0):
        call_set.add(m.group(1))
    for m in re.finditer(r"::\s*([A-Za-z_]\w*)\s*\(", src0):
        call_set.add(m.group(1))
    calls = sorted(list(call_set))[:18]

    return namespace, uses, consts, defines, functions, classes, calls

def render_map_php(filename: str, namespace: str, uses, consts, defines, functions, classes, calls, todo_lines: List[str]) -> str:
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
        for kind, name, ln, methods in classes:
            lines.append(f"- {kind} {name}  [L{ln}]")
            for mn, args, mln in methods[:60]:
                lines.append(f"    - function {mn}({args})  [L{mln}]")
            if len(methods) > 60:
                lines.append(f"    - ... (+{len(methods)-60} more)")
        lines.append("")

    if functions:
        lines.append("## Functions")
        for name, args, ln in functions[:200]:
            lines.append(f"- function {name}({args})  [L{ln}]")
        if len(functions) > 200:
            lines.append(f"- ... (+{len(functions)-200} more)")
        lines.append("")

    if calls:
        lines.append("## Call Hints (-> / ::)")
        lines.append("- " + ", ".join(calls))
        lines.append("")

    return "\n".join(lines)

def extract_map_kotlin_lite(source: str):
    lines = source.splitlines()
    pkg = ""
    imports: List[Tuple[str, int]] = []
    decls: List[Tuple[str, int]] = []   # class/object/interface/enum
    funs: List[Tuple[str, int]] = []    # fun signatures (line)
    companions: List[int] = []

    # --- NEW: collect annotations right above a declaration line ---
    def _collect_prev_annotations(idx_1based: int, lookback: int = 6) -> List[str]:
        """
        idx_1based: current line number (1-based) where 'fun ...' was found.
        We look upward a few lines and collect '@Something' lines.
        Stops when it hits a non-annotation / non-empty line (like '{', '}', 'val', etc.)
        """
        tags: List[str] = []
        j = idx_1based - 1  # convert to 0-based index for list access
        start = max(0, j - lookback)

        # walk upward from just above current line
        k = j - 1
        while k >= start:
            t = lines[k].strip()
            if not t:
                # allow blank lines between annotations and fun? (usually no, but safe)
                k -= 1
                continue

            # ignore file-level annotations
            if t.startswith("@file:"):
                k -= 1
                continue

            # if it's an annotation line, collect it
            if t.startswith("@"):
                # take only the annotation "name" part: @OptIn(...) -> OptIn
                # and keep a compact form with leading '@'
                m = re.match(r"^@([A-Za-z_]\w*)", t)
                if m:
                    tags.append("@" + m.group(1))
                k -= 1
                continue

            # if it's not an annotation line, stop scanning (we reached code)
            break

        # reverse because we collected bottom-up
        tags.reverse()

        # OPTIONAL: limit count to keep output compact
        if len(tags) > 4:
            tags = tags[:4] + ["@..."]
        return tags

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue

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

        # fun / override fun (+ modifiers)
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

            # --- NEW: annotation tags for this function ---
            ann = _collect_prev_annotations(i, lookback=6)
            ann_tag = f" [{' '.join(ann)}]" if ann else ""

            sig = f"fun {name}({args}){ann_tag}"
            funs.append((sig, i))
            continue

    return pkg, imports, decls, funs, companions


def render_map_kotlin(filename: str, pkg: str, imports, decls, funs, companions, todo_lines: List[str]) -> str:
    out: List[str] = []
    out.append("### CODE MAP (READ-ONLY) ###")
    out.append(f"File: {filename or '-'}")
    out.append("Mode: Kotlin-lite (line scan)")
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
        if len(decls) > 160:
            out.append(f"- ... (+{len(decls)-160} more)")
        out.append("")

    if companions:
        out.append("## Companion Object")
        for ln in companions[:40]:
            out.append(f"- companion object  [L{ln}]")
        if len(companions) > 40:
            out.append(f"- ... (+{len(companions)-40} more)")
        out.append("")

    if funs:
        out.append("## Functions (lite)")
        for sig, ln in funs[:220]:
            out.append(f"- {sig}  [L{ln}]")
        if len(funs) > 220:
            out.append(f"- ... (+{len(funs)-220} more)")
        out.append("")

    return "\n".join(out)

def extract_map_java_lite(source: str):
    lines = source.splitlines()
    pkg = ""
    imports: List[Tuple[str, int]] = []
    decls: List[Tuple[str, int]] = []   # class/interface/enum/record
    methods: List[Tuple[str, int]] = [] # method-like signatures

    decl_re = re.compile(r"^(?:public\s+)?(?:abstract\s+|final\s+)?(class|interface|enum|record)\s+([A-Za-z_]\w*)")
    # Rough method: visibility + return + name(params)
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

        # Skip obvious control-flow lines to reduce noise
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
        if len(decls) > 180:
            out.append(f"- ... (+{len(decls)-180} more)")
        out.append("")

    if methods:
        out.append("## Methods (lite)")
        for sig, ln in methods[:260]:
            out.append(f"- {sig}  [L{ln}]")
        if len(methods) > 260:
            out.append(f"- ... (+{len(methods)-260} more)")
        out.append("")

    return "\n".join(out)

def sniff_lite_language(source: str, filename: str = "") -> str:
    """
    Smarter lite language sniff:
    - Uses filename extension when available
    - Uses strong headers/signals
    - Falls back to weighted scoring
    Returns: 'PHP' | 'Kotlin-lite' | 'Java-lite'
    """
    fn = (filename or "").lower().strip()

    # 1) Extension hard hints (strongest, if reliable)
    if fn.endswith(".php"):
        return "PHP"
    if fn.endswith(".kt") or fn.endswith(".kts"):
        return "Kotlin-lite"
    if fn.endswith(".java"):
        return "Java-lite"

    head = "\n".join(source.splitlines()[:120])
    head_l = head.lower()
    s = source

    # 2) Strong header / structural signals
    if "<?php" in head_l:
        return "PHP"

    # Kotlin package/import style
    if re.search(r"^\s*package\s+[a-zA-Z_][\w.]*\s*$", head, flags=re.M):
        # could be Kotlin or Java, but Kotlin often has "fun"/"object"/"companion"
        if re.search(r"\b(fun|companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s):
            return "Kotlin-lite"

    # Java package/import style
    if re.search(r"^\s*package\s+[a-zA-Z_][\w.]*\s*;\s*$", head, flags=re.M):
        return "Java-lite"

    # 3) Weighted scoring (lite, robust)
    score = {"PHP": 0, "Kotlin-lite": 0, "Java-lite": 0}

    # --- PHP signals ---
    # variable sigil, arrows, scope resolution, common keywords
    score["PHP"] += 4 * len(re.findall(r"\$\w+", s))
    score["PHP"] += 3 * len(re.findall(r"->\s*[A-Za-z_]\w*\s*\(", s))
    score["PHP"] += 3 * len(re.findall(r"::\s*[A-Za-z_]\w*\s*\(", s))
    if re.search(r"^\s*namespace\s+[^;{]+\s*;", s, flags=re.M):
        score["PHP"] += 8
    if re.search(r"^\s*use\s+[^;]+\s*;", s, flags=re.M):
        score["PHP"] += 4
    if re.search(r"\bfunction\s+\w+\s*\(", s):
        score["PHP"] += 2
    if re.search(r"\bclass\s+\w+", s):
        score["PHP"] += 1
    if re.search(r"\b(public|protected|private)\b", s):
        score["PHP"] += 1

    # --- Kotlin signals ---
    if re.search(r"^\s*fun\s+\w+\s*\(", s, flags=re.M):
        score["Kotlin-lite"] += 10
    if re.search(r"\boverride\s+fun\s+\w+\s*\(", s):
        score["Kotlin-lite"] += 8
    if re.search(r"\b(companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s):
        score["Kotlin-lite"] += 8
    if re.search(r"^\s*import\s+[\w.]+\s*$", s, flags=re.M):
        score["Kotlin-lite"] += 2
    if re.search(r"\bval\s+\w+\s*[:=]|\bvar\s+\w+\s*[:=]", s):
        score["Kotlin-lite"] += 3
    if re.search(r"\bwhen\s*\(", s):
        score["Kotlin-lite"] += 2
    if re.search(r"@Composable\b", s):
        score["Kotlin-lite"] += 6

    # --- Java signals ---
    if re.search(r"^\s*import\s+[\w.]+\s*;\s*$", s, flags=re.M):
        score["Java-lite"] += 4
    if re.search(r"\b(public|protected|private)\s+(class|interface|enum|record)\s+\w+", s):
        score["Java-lite"] += 9
    if re.search(r"\b(class|interface|enum|record)\s+\w+", s):
        score["Java-lite"] += 4
    if re.search(r"\bstatic\b", s):
        score["Java-lite"] += 2
    if re.search(r"\bnew\s+\w+", s):
        score["Java-lite"] += 1
    # common Java method pattern (rough)
    if re.search(r"^\s*(public|protected|private)\s+[\w<>\[\]]+\s+\w+\s*\(", s, flags=re.M):
        score["Java-lite"] += 3

    # Tie-breakers:
    # - If PHP has any '$' presence at all, bias to PHP in close calls.
    # - Kotlin wins over Java when both have package/import but Kotlin-specific tokens exist.
    best = max(score, key=score.get)
    best_val = score[best]
    sorted_scores = sorted(score.items(), key=lambda x: x[1], reverse=True)

    if best_val == 0:
        return "Kotlin-lite"  # safe default

    # close-call handling
    if len(sorted_scores) >= 2:
        top, second = sorted_scores[0], sorted_scores[1]
        if top[1] - second[1] <= 2:
            if re.search(r"\$\w+", s):
                return "PHP"
            if re.search(r"\b(fun|companion\s+object|data\s+class|sealed\s+class|object\s+)\b", s):
                return "Kotlin-lite"

    return best


# ============================
# Redaction (safety)
# ============================

_RE_SSN = re.compile(r"\b\d{6}-\d{7}\b")
_RE_PHONE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")
_RE_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

_RE_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    ^(\s*)
    ([A-Z0-9_]*(TOKEN|API[_-]?KEY|SECRET|PASSWORD|PASS|AUTH|BEARER)[A-Z0-9_]*)
    (\s*=\s*)
    (.+?)\s*$
    """
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
# GUI (v2.2)
# ============================

class MiniMapPadV22(tk.Tk):
    def __init__(self):
        super().__init__()
        # --- DPI awareness (Windows) ---
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()   # Legacy fallback
            except Exception:
                pass

        self.title("MiniMapPad v2.2 — Code Map (Auto Copy)")
        self.geometry("1260x860")

        self.current_file = ""

        # Options (defaults)
        self.var_auto_copy = tk.BooleanVar(value=True)
        self.var_topmost = tk.BooleanVar(value=False)
        self.var_redact = tk.BooleanVar(value=True)
        self.var_todo = tk.BooleanVar(value=True)
        self.var_dark = tk.BooleanVar(value=True)

        # NEW: Language selector
        # Auto: Python AST -> success => Python, else => lite fallback (no popup)
        self.var_lang = tk.StringVar(value="Auto")
        self.lang_choices = ["Auto", "Python", "PHP", "Kotlin-lite", "Java-lite"]

        # Fonts
        self.font_ui = ("Segoe UI Mono", 12)
        self.font_btn = ("Segoe UI Mono", 12, "bold")
        self.font_text = ("Segoe UI Mono", 13)

        self._build_ui()
        self.apply_theme()  # initial theme

    def _build_ui(self):
        # top bar
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(10, 6))

        btn_generate = tk.Button(bar, text="Generate Map", command=self.generate, font=self.font_btn, width=12)
        btn_generate.pack(side="left")

        btn_copy = tk.Button(bar, text="Copy Output", command=self.copy_result, font=self.font_ui, width=11)
        btn_copy.pack(side="left", padx=6)

        btn_open = tk.Button(bar, text="Open File (optional)", command=self.open_file, font=self.font_ui, width=16)
        btn_open.pack(side="left")

        # options
        opt = tk.Frame(bar)
        opt.pack(side="left", padx=14)

        tk.Checkbutton(opt, text="Auto-copy on Generate", variable=self.var_auto_copy, font=self.font_ui).pack(side="left")
        tk.Checkbutton(opt, text="Redact secrets/PII", variable=self.var_redact, font=self.font_ui).pack(side="left", padx=8)
        tk.Checkbutton(opt, text="TODO warnings", variable=self.var_todo, font=self.font_ui).pack(side="left")
        tk.Checkbutton(opt, text="Always on top", variable=self.var_topmost, command=self.apply_topmost, font=self.font_ui).pack(side="left", padx=8)
        tk.Checkbutton(opt, text="Dark mode", variable=self.var_dark, command=self.apply_theme, font=self.font_ui).pack(side="left", padx=6)

        # NEW: language dropdown
        tk.Label(opt, text="Language:", font=self.font_ui).pack(side="left", padx=(14, 4))
        dd = tk.OptionMenu(opt, self.var_lang, *self.lang_choices)
        dd.configure(font=self.font_ui)
        dd.pack(side="left")

        # status line
        self.lbl_status = tk.Label(
            self,
            text="Paste code → Generate Map → Auto-copy → Paste to LLM (Ctrl+V).  (Auto: Python AST then lite fallback)",
            font=self.font_ui,
            anchor="w",
            justify="left"
        )
        self.lbl_status.pack(fill="x", padx=12, pady=(0, 8))

        # main split pane
        pane = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief="raised", bd=0)
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # input block
        f_in = tk.Frame(pane)
        tk.Label(f_in, text="Step1 - Paste code here", font=self.font_ui).pack(anchor="w", pady=(0, 6))
        self.txt_in = tk.Text(f_in, height=18, wrap="none", undo=True, font=self.font_text)
        self.txt_in.pack(fill="both", expand=True, pady=(0, 10))
        pane.add(f_in)

        # output block
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
            bg = "#0f0f0f"
            fg = "#e6e6e6"
            sub = "#2a2a2a"
            text_bg = "#1E1F23"
            text_fg = "#E1E1E1"
            insert = "#ffffff"
        else:
            bg = "#f5f5f5"
            fg = "#111111"
            sub = "#ffffff"
            text_bg = "#ffffff"
            text_fg = "#111111"
            insert = "#111111"

        self.configure(bg=bg)

        for w in self.winfo_children():
            self._apply_widget_theme(w, bg, fg, sub, text_bg, text_fg, insert)

        # per-text tuning (Step1 brighter than Step2)
        if dark:
            in_bg = "#2a2b30"
            out_bg = "#1E1F23"
            fg = "#E1E1E1"
            insert = "#ffffff"
        else:
            in_bg = "#ffffff"
            out_bg = "#ffffff"
            fg = "#111111"
            insert = "#111111"

        try:
            self.txt_in.configure(bg=in_bg, fg=fg, insertbackground=insert)
            self.txt_out.configure(bg=out_bg, fg=fg, insertbackground=insert)
        except Exception:
            pass

    def _apply_widget_theme(self, w, bg, fg, sub, text_bg, text_fg, insert):
        cls = w.winfo_class()

        if cls in ("Frame", "PanedWindow"):
            try:
                w.configure(bg=bg)
            except Exception:
                pass

        if cls == "Label":
            try:
                w.configure(bg=bg, fg=fg)
            except Exception:
                pass

        if cls == "Button":
            try:
                w.configure(bg=sub, fg=fg, activebackground=sub, activeforeground=fg)
            except Exception:
                pass

        if cls == "Checkbutton":
            try:
                w.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=bg)
            except Exception:
                pass

        if cls == "Text":
            try:
                w.configure(bg=text_bg, fg=text_fg, insertbackground=insert)
            except Exception:
                pass

        # OptionMenu is a Menubutton internally
        if cls in ("Menubutton",):
            try:
                w.configure(bg=sub, fg=fg, activebackground=sub, activeforeground=fg)
            except Exception:
                pass

        for c in w.winfo_children():
            self._apply_widget_theme(c, bg, fg, sub, text_bg, text_fg, insert)

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("Code files", "*.py *.php *.kt *.java *.txt"),
                ("Python files", "*.py"),
                ("PHP files", "*.php"),
                ("Kotlin files", "*.kt"),
                ("Java files", "*.java"),
                ("All files", "*.*"),
            ]
        )
        if not path:
            return
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
            self.title(f"MiniMapPad v2.2 — ✅ Copied ({mode_label})")
            self.set_status(f"✅ Generated + copied. Mode: {mode_label}. Now paste into ChatGPT/Claude (Ctrl+V).")
        else:
            self.title(f"MiniMapPad v2.2 — Generated ({mode_label})")
            self.set_status(f"✅ Generated. Mode: {mode_label}. (Auto-copy is OFF)")

    def generate(self):
        src = self.txt_in.get("1.0", "end").strip()
        if not src:
            messagebox.showwarning("Info", "Input is empty. Paste code first.")
            return

        todo_lines = _find_todo_lines(src, limit=12) if self.var_todo.get() else []
        fname = os.path.basename(self.current_file) if self.current_file else ""

        selected = self.var_lang.get()

        # ---- Forced modes ----
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
                ns, uses, consts, defines, functions, classes, calls = extract_map_php_lite(src)
                out = render_map_php(fname, ns, uses, consts, defines, functions, classes, calls, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "PHP-lite")
            except Exception as e:
                # No disruptive popup for lite modes: keep flow
                out = "### CODE MAP (READ-ONLY) ###\nMode: PHP-lite\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "PHP-lite (partial)")
            return

        if selected == "Kotlin-lite":
            try:
                pkg, imports, decls, funs, companions = extract_map_kotlin_lite(src)
                out = render_map_kotlin(fname, pkg, imports, decls, funs, companions, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "Kotlin-lite")
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

        # ---- Auto mode ----
        # 1) Try Python AST first. If fails, fallback to lite (NO SyntaxError popup).
        if selected == "Auto":
            try:
                imports, constants, globals_lite, functions, classes = extract_map_python_ast(src)
                out = render_map_python(fname, imports, constants, globals_lite, functions, classes, todo_lines)
                out = self._write_output(out)
                self._finalize_copy(out, "Auto → Python (AST)")
                return
            except SyntaxError:
                # Silent fallback (no popup)
                pass
            except Exception:
                # Silent fallback (no popup)
                pass

            lite = sniff_lite_language(src, fname)
            try:
                if lite == "PHP":
                    ns, uses, consts, defines, functions, classes, calls = extract_map_php_lite(src)
                    out = render_map_php(fname, ns, uses, consts, defines, functions, classes, calls, todo_lines)
                    out = self._write_output(out)
                    self._finalize_copy(out, "Auto → PHP-lite (Python parse failed)")
                elif lite == "Java-lite":
                    pkg, imports, decls, methods = extract_map_java_lite(src)
                    out = render_map_java(fname, pkg, imports, decls, methods, todo_lines)
                    out = self._write_output(out)
                    self._finalize_copy(out, "Auto → Java-lite (Python parse failed)")
                else:
                    pkg, imports, decls, funs, companions = extract_map_kotlin_lite(src)
                    out = render_map_kotlin(fname, pkg, imports, decls, funs, companions, todo_lines)
                    out = self._write_output(out)
                    self._finalize_copy(out, "Auto → Kotlin-lite (Python parse failed)")
            except Exception as e:
                out = "### CODE MAP (READ-ONLY) ###\nMode: Auto (lite fallback)\n\n[Lite scan failed]\n" + str(e)
                out = self._write_output(out)
                self._finalize_copy(out, "Auto → lite (partial)")
            return

        # Fallback safety (should not hit)
        messagebox.showerror("Error", f"Unknown language mode: {selected}")

    def copy_result(self):
        out = self.txt_out.get("1.0", "end").strip()
        if not out:
            messagebox.showwarning("Info", "No output to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(out)
        self.title("MiniMapPad v2.2 — 📋 Copied")
        self.set_status("📋 Copied output to clipboard.")

if __name__ == "__main__":
    app = MiniMapPadV22()
    app.mainloop()
