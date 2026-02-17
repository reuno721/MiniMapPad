# MiniMapPad — Code Map Generator (Auto Copy)

Turn long source code into a **compact structure map** you can paste into ChatGPT / Claude / Gemini.
Designed for people who don’t want to manually trim code every time.

✅ **Primary support:** Python (AST) / PHP (lite)  
🟡 **Secondary:** Kotlin / Java (lite)  
📋 **Workflow:** Paste → Generate → Auto-copy → Paste to LLM

---

## Why MiniMapPad?

LLMs often get confused when you paste a huge file (1,000–5,000+ lines).  
MiniMapPad generates a **read-only “Code Map”**:

- imports / namespace / use
- constants / defines (PHP)
- functions + line numbers
- classes + methods + line numbers
- small call hints (PHP: `->` / `::`)
- optional TODO warnings

This helps the model ask for **only the needed function blocks** instead of guessing.

---

## Output Example

MiniMapPad outputs something like:

- `class MainActivity [L191]`
- `fun onCreate(savedInstanceState: Bundle?) [L197]`
- `function foo($a, $b) [L120]`
- `class Foo::bar($x) [L88]`

> Rule: The map is structure-only. Do NOT rewrite code.

---

## Features

- **Python AST map (accurate)**
- **PHP lite map (regex/token scan)** — low overhead, practical
- **Kotlin/Java lite mode** — never blocks your flow
- **Auto mode**
  - Try Python AST first
  - If parse fails, silently fallback to lite mode
- **Auto-copy to clipboard**
- Optional **secret/PII redaction**
- Optional **TODO/FIXME/HACK/TEMP** warnings
- Dark mode UI

---

## Quick Start

### Option A) Run from Python
Requirements: Python 3.9+

```bash
python minimappad_v2_2.py
