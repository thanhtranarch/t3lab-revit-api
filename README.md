# T3Lab — pyRevit Extension for Autodesk Revit

[![Revit Version](https://img.shields.io/badge/Revit-2020%2B-blue.svg)](https://www.autodesk.com/products/revit/overview)
[![pyRevit](https://img.shields.io/badge/pyRevit-4.8%2B-orange.svg)](https://github.com/eirannejad/pyRevit)
[![CPython](https://img.shields.io/badge/CPython-3.12%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

T3Lab is a BIM automation and intelligence framework for Autodesk Revit, built on the **T3Lab Master Architecture System (MAS)**. It bridges traditional BIM workflows with AI assistance and batch automation.

Tools are consolidated rather than scattered: instead of dozens of single-purpose buttons, related workflows are merged into unified `Mana*` managers (ManaAnno, ManaSelect, ManaSheets, …) — one window, tabbed modes, shared state.

---

## Architecture: T3Lab MAS 3.0

Three layers form a self-sustaining ecosystem for architectural intelligence:

| Layer | Description |
|-------|-------------|
| **Intelligence** | T3Lab Assistant — bilingual VI/EN language analysis, graph-based agent orchestration, RAG over project + Revit API knowledge, and multi-provider LLM routing (Ollama, LM Studio, Claude, OpenAI, DeepSeek). **Local-first**: a new install defaults to Qwen on Ollama, with an optional self-study loop that distils the office's own successful commands back into the local model. |
| **Execution** | 42 ribbon-integrated tools organized by discipline across 7 panels |
| **Data Fabric** | MCP server bridge for external agents (Claude Desktop, ChatGPT/Codex and Antigravity can drive Revit); Vercel cloud API for family metadata; hybrid local/cloud storage |

---

## Ribbon: T3Lab Tab

The tab exposes **7 panels**. Buttons marked *(DQT)* were developed in collaboration with Dang Quoc Truong.

### Standard

| Tool | Description |
|------|-------------|
| **Auto Work** | Automation recorder & player — quick click at a fixed coordinate, or record and replay a full mouse sequence with timing. |
| **UI Showcase** | Reference window for the T3Lab Lumina design standard (palette, typography, buttons, inputs). |

### Annotation & Select

| Tool | Description |
|------|-------------|
| **Mana Anno** | Unified Find / Remove / Rename manager for Dimensions and Text Notes. *(DQT)* |
| **Auto Dimension** | Automatic dimension chains for walls, columns, doors, lifts and grids in the active or a chosen view. |
| **Mana DWG** | CAD import and CAD link manager — list, rename and delete DWG imports/links. *(DQT)* |
| **Mana Select** | Consolidated selection manager: Quick Select by parameter/text, Select Similar by type/family/category, and linked-element selection. |

### Modeling & Datum

| Tool | Description |
|------|-------------|
| **CAD to BIM** (pulldown) | **CAD to Elements** (map DWG layers → Walls / Floors / Beams), **Point Cloud to Model** (Scan-to-BIM wizard detecting walls, floors, ceilings, doors, windows, columns, stairs, roofs), **Room To Floor**, **Door Threshold**, **Image to Drafting**, **Text to Element**. |
| **Property Line** | Type any address worldwide and draw its property boundary: OpenStreetMap everywhere (no API key), LightBox cadastral parcels for US addresses. |
| **Tile Layout** | 3-step wizard: extract floor boundaries, pick a tile pattern per floor, generate and place a tiled layout. |
| **Element Adjust** (pulldown) | **Auto Join** (rule-based joining, Shift+Click for defaults) *(DQT)*, **Split Elements** at levels, **Wall Cut Profile** from linked-model intersections, **Auto Adj Base Offset**. |
| **FamiGen** | Family generator — from CAD blocks (DWG → .rfa), from a JSON schema, or from built-in batch presets. |
| **Mana Fami** | Family manager — browse by category, search/filter, and load families from disk. *(DQT)* |

### Views & Sheets

| Tool | Description |
|------|-------------|
| **BatchOut** | Batch export sheets to PDF, DWG, NWD and IFC with revision tracking and advanced options. |
| **Mana Views** | View manager — rename views, batch rename, apply and update view templates. |
| **Mana Sheets** | Sheet manager — Excel sync, view placement, sheet sets, re-numbering. |
| **SheetGen** | Generate floor-plan views from a room list via a WPF selection interface. |

### Data & IFC-SG

| Tool | Description |
|------|-------------|
| **Mana Sched** | Schedule manager — export to Excel with formatting, import values back, duplicate schedules. |
| **Mana Para** | Parameter manager — transfer values by rule, Text-to-Element assignment, values-to-filled-region. |
| **Mana Contains** | Spatial containment — find elements inside Rooms/Areas/Spaces/Zones/Masses/Scope Boxes, push container values down or aggregate element data up. |
| **BCF Reader** | Modeless BCF issue browser (IFC Delta Viewer exports) — click an issue to navigate the view. |
| **Foundation Volume** | Write computed volume of Structural Foundations into a chosen shared parameter. |
| **IFC-SG Suite** | Subtype Assigner (Excel mapping → IFC Export Class & Predefined Type) + Compliance Checker against CORENET X rules. |

### Standards & Settings

| Tool | Description |
|------|-------------|
| **Mana Styles** | Fill patterns, line styles, line patterns and visual colour-splashing in one window. |
| **Mana Workset** | Enable worksharing, create/delete/purge worksets, generate workset view filters. |
| **Mana Loca** | Modeless element location editor — read and edit XYZ in a grid, commit in one transaction. |
| **Model Auditor** | Consolidated model health check, warnings, in-place models and material audit. |

### Support

| Tool | Description |
|------|-------------|
| **T3Lab Assistant** | Natural-language AI assistant — drive T3Lab tools via Vietnamese/English chat. |
| **PDF Import** | Import PDF pages into selected Revit views sequentially. |
| **Assistant Tools** (stack) | **MCP Control** (start/stop the MCP server, connection settings, one-click auto-configure for Claude Desktop / ChatGPT (Codex) / Antigravity), **LLMs Setting** (provider, model, API key), **Feedback**. |
| **UI Theme & Tabs** (stack) | **BG Theme** (HSV picker with eyedropper, gradient 3D backgrounds, Light/Dark UI for Revit 2024+), **Mana Tabs** (hide/show ribbon tabs), **Ribbon Names** (shorten/restore tab names). |
| **Cloud Links** (stack) | Autodesk Forma, Autodesk Health, Bluebeam Status. |

---

## T3Lab Assistant

The Assistant is the largest subsystem in the repo. It chats in Vietnamese or
English, answers from project knowledge, and calls T3Lab tools on the live model
through the MCP bridge. Everything under `lib/Intelligence/` is **pure Python** —
no Revit API or WPF imports — so it runs under CPython 3 inside Revit and
under CPython 3 for the `dev/` test suites.

### Providers — local-first

A fresh install seeds `active_provider = ollama` and auto-picks a tool-capable
Qwen tier (`qwen3:14b → 8b → 4b`), so the Assistant works with no API key and no
data leaving the machine. A saved choice always wins on restore, and the cloud
fallback chain (Claude / OpenAI / DeepSeek / LM Studio) is unchanged — switch
under **Support → Assistant Tools → LLMs Setting**
([setting flow](docs/assistant-llms-setting-flow.md)).

### Language layer — `lib/Intelligence/language/`

One bilingual analysis per turn, shared by every consumer: language detection on
mixed VI/EN text, typo and teencode normalisation, a bilingual entity dictionary
(categories, colours, levels, sheets, measurements normalised to mm), and
negation / modality / scope / risk semantics. Diacritic-stripping merges words
that mean different things (`từ`→`tu`→`tủ` = Casework), so single-syllable
Vietnamese aliases are matched **with** diacritics while multi-syllable aliases
still match undiacriticised input. Normalised text is used for **routing signals
only** — what reaches the model, the history and the transcript is always the
user's verbatim text.

### Graph agent layer — `lib/Intelligence/graph/`

A chat turn used to be a straight line: one classification → one specialist →
one agent loop → one answer, which silently dropped the second half of a
two-goal request. The graph layer maps that onto plan · dispatch · execute ·
observe · adapt — `planner.py`, `router.py`, `executor.py`, `reducer.py`,
`verifier.py`, `observability.py` over the `primitives.py` NODE/EDGE/STATE/
CONTEXT/MEMORY types and 8 topology patterns. Independent **read** goals fan out
in parallel; **write** goals stay ordered behind everything the user said before
them. The planner is deliberately conservative — it returns a single node rather
than split a sentence it is unsure about. Details:
[`docs/assistant-graph-architecture.md`](docs/assistant-graph-architecture.md).

### Knowledge & RAG — `lib/Intelligence/knowledge/`

BM25 + embedding retrieval over project documents with a query builder,
reranking, chunking, and a rolling context digest of the open model. PDF
extraction is memoised per `(file, mtime, size)` so a rescan reads a network
share once instead of twice, and owner-password-protected PDFs (the usual shape
of a published BEP or manual) are decrypted rather than mistaken for scans.
Long conversations are **folded** into a running summary instead of dropped, so
a constraint stated on turn 2 survives to turn 20.

### Self-study & local fine-tune

| Stage | Where | What |
|-------|-------|------|
| Curate | `lib/Intelligence/learning/` | While Revit is idle, enrichers turn successful commands, telemetry, model snapshots, API facts and MCP sessions into a chat-SFT corpus at `%APPDATA%/T3LabAI/training/dataset.jsonl`. On by default (`agents.self_study`), heavily throttled, zero API cost. |
| Teach (optional) | MCP Control → Teaching Capture | Connect Claude Desktop (Opus) to Revit over the MCP bridge and demonstrate tasks; each `t3lab_begin_teaching` … `t3lab_end_teaching` sequence is stored as an agentic trajectory. A scratch `.rvt` must be marked as the **sandbox** — model writes are blocked everywhere else while teaching is on. |
| Train | `tools/train/` | CPython 3 + GPU LoRA fine-tune (Unsloth) → GGUF + Modelfile → `ollama create t3lab-assistant`. Runs outside Revit; `--dry-run` works with no GPU. See [`tools/train/README.md`](tools/train/README.md). |
| Distribute | `lib/Intelligence/config/teacher_exemplars.json` | Weights live in one machine's Ollama. The portable path distils the teacher data into a small git-tracked few-shot file injected into the local model's system prompt (~2 KB, local models only, static so the prompt cache still hits) — commit it and a plain `qwen3:14b` on any machine answers in the taught style with no re-train. |

`agents.opus_teacher` (off by default) is the one enricher that spends API
tokens: it asks Opus for gold answers to curated Revit/BIM questions while the
Assistant itself keeps chatting on local Qwen. The skill pack
`skills/train-t3lab-model/` drives the whole loop from Claude Desktop.

### Telemetry

`telemetry.py` appends one JSONL line per turn to
`%APPDATA%/T3LabAI/telemetry/<date>.jsonl` — time-to-first-token, total turn
time, Revit round-trips per turn, token counts and prompt-cache hits. Nothing is
uploaded. Benchmark with `python3 dev/bench_assistant.py`; the measured
optimisations are written up in
[`docs/assistant-performance.md`](docs/assistant-performance.md).

### UI note

The Assistant is a **chat surface**, not a tool dialog — it is the one window
that does not use the Lumina palette. Every token comes from `GUI/RevitTheme.py`
and follows Revit's own light/dark UI theme
([`docs/assistant-revit-ui.md`](docs/assistant-revit-ui.md)). It is UI-locked;
do not re-apply Lumina to `T3LabAssistant.xaml`.

---

## Project Structure

```
t3lab-revit-api/
├── T3Lab.extension/
│   ├── T3Lab.tab/
│   │   ├── Standard.panel/
│   │   ├── Annotation & Select.panel/
│   │   ├── Modeling & Datum.panel/
│   │   ├── Views & Sheets.panel/
│   │   ├── Data & IFC-SG.panel/
│   │   ├── Standards & Settings.panel/
│   │   └── Support.panel/
│   ├── lib/
│   │   ├── GUI/                    # WPF dialogs (XAML + Python classes)
│   │   │   ├── Tools/              # 54 tool window views (.xaml)
│   │   │   └── Resources/          # Shared WPF styles (WPF_styles.xaml)
│   │   ├── Intelligence/           # AI engine (pure Python — no Revit/WPF imports)
│   │   │   ├── language/           # Bilingual VI/EN analysis: detect, normalise, entities
│   │   │   ├── graph/              # Graph agents: plan · route · execute · reduce · verify
│   │   │   ├── knowledge/          # RAG: BM25 + embeddings, rerank, PDF cache, context digest
│   │   │   ├── learning/           # Idle-time self-study, enrichers, SFT dataset, exemplars
│   │   │   ├── agents/             # Dispatcher, specialists, task manager
│   │   │   ├── skills/             # Instruction packs (.md) activated per request
│   │   │   └── config/             # Learned patterns, feedback, teacher exemplars
│   │   ├── Services/               # Exporters, MCP service, Revit context, spell checker
│   │   ├── Selection/              # Element selection helpers
│   │   ├── Renaming/               # Renaming engine classes
│   │   ├── Snippets/               # 22 reusable Revit API code snippets
│   │   ├── Utils/                  # CAD/family helpers
│   │   ├── config/                 # Settings, project store, user profile
│   │   ├── core/                   # MCP server, ExternalEvent bridge, teaching capture, paths
│   │   └── ui/                     # Button states, settings dialog
│   ├── checks/                     # Model checker script validations
│   ├── commands/                   # Standalone command scripts
│   ├── hooks/                      # pyRevit event hooks
│   └── startup.py                  # Extension startup
├── api/                            # Cloud serverless functions (family metadata)
├── dev/                            # Dev utilities, audits, plans, 22 test suites
├── docs/                           # Documentation
├── skills/                         # Claude Desktop skill packs (train-t3lab-model)
├── tools/train/                    # Out-of-Revit LoRA fine-tune pipeline (CPython 3 + GPU)
└── scripts/                        # Reload, cache-clearing, icon generation
```

---

## Library Highlights

### `lib/Intelligence/`
- `t3lab_assistant.py` — main AI assistant engine
- `t3lab_agent.py` / `agent_loop.py` — agentic tool-calling loop
- `nlu_engine.py` — Natural Language Understanding for Vietnamese / English
- `routing.py` / `llm_router.py` — testable routing ladder and provider selection
- `claude_provider.py`, `openai_provider.py`, `deepseek_provider.py`,
  `ollama_provider.py`, `lmstudio_provider.py` — pluggable LLM backends
  ([setting flow](docs/assistant-llms-setting-flow.md))
- `tool_schema.py` — converts the MCP tool registry into each provider's native
  function-calling format, instead of dumping schemas into the system prompt
- `rag_processor.py` — Retrieval-Augmented Generation over Revit API context
- `conversation.py` — bounded turn window with a rolling summary (fold, don't drop)
- `feedback.py` — turns 👍/👎 into routes the assistant actually changes
- `telemetry.py` — per-turn latency, round-trip and prompt-cache accounting
- `link_reader.py` — resolves pasted UNC / URL / local paths into readable attachments
- `skills_engine.py` — instruction packs that activate on a request
- `skill_installer.py` — installs Claude-format skills from a GitHub repo link
  ([docs](docs/assistant-skills-from-github.md))

### `lib/core/`
`server.py` and `bridge.py` implement the thread-safe local MCP server (dynamic port allocation from `48884`) and the ExternalEvent bridge that marshals agent calls onto the Revit API thread. `teaching.py` records demonstrated tool-use trajectories when Teaching Capture is on; the teaching tools (`t3lab_set_teaching_mode`, `t3lab_mark_sandbox`, `t3lab_begin_teaching` / `t3lab_end_teaching`, `t3lab_training_status`, `t3lab_train_model`, `t3lab_build_exemplars`) are exposed to external MCP clients only and hidden from the in-app assistant.

### `lib/Snippets/`
22 reusable Python patterns covering annotations, bounding boxes, context managers, unit conversion, element manipulation, Excel integration, filtered element collectors, filters, geometry probing, groups, host lookup, lines, graphic overrides, revisions, selection, similar-element matching, sheets, text and views.

### `pyRevit UI Design System/`
The **only** UI standard: `T3LAB_UI_STANDARD.md` (tokens, 7 type sizes, spacing, 10 layout rules, 5 patterns) and `T3Lab.Styles.xaml` (82 `T3.*` resource keys). Every new tool follows `.claude/rules/new-tool-standard.md`; `python3 dev/audit_t3.py` is the gate. The daily governance routine lives in `docs/ui-governance/`.

### `lib/GUI/Resources/WPF_styles.xaml`
Legacy, frozen. Shared button styles of the retired Lumina system, still embedded in the 51 XAMLs not yet migrated. No longer synced — the block disappears file by file as migration proceeds.

### `checks/`
| Script | Description |
|--------|-------------|
| `modelchecker_check.py` | Comprehensive model quality checks |
| `modelchecker_Warnings_check.py` | Warning-specific validation |
| `refplanes_check.py` | Reference plane validation |
| `schedules_not_on_sheet_check.py` | Identifies schedules not placed on sheets |
| `badgeometry_check.py` | Detects problematic element geometry |

---

## Setup & Installation

1. Clone this repository into your pyRevit extensions folder:
   ```
   %APPDATA%\pyRevit\Extensions\T3Lab.extension
   ```
2. Ensure **pyRevit 4.8+** is installed.
3. Reload pyRevit — the **T3Lab** tab will appear in the Revit ribbon.
4. *(Assistant, optional)* Install [Ollama](https://ollama.com) and pull the
   recommended local model — no API key needed, nothing leaves the machine:
   ```
   ollama pull qwen3:14b
   ```
   For a cloud provider instead, set the provider and API key under
   **Support → Assistant Tools → LLMs Setting**.

---

## Development

| Command | Purpose |
|---------|---------|
| `python3 dev/audit_tools.py --quiet` | Audit pushbutton bundles and script structure |
| `python3 dev/audit_t3.py --quiet` | Audit XAML against the T3 UI standard (gate) |
| `python3 dev/audit_t3.py --legacy` | List migration debt of the XAMLs not yet on T3 |
| `python3 dev/audit_t3.py --file <path>` | Audit one XAML, full enforcement |
| `python3 dev/test_<suite>.py` | Run a test suite — each is standalone, exits non-zero on failure |
| `python3 dev/bench_assistant.py` | Benchmark assistant turn latency and token usage |
| `python3 tools/train/validate_dataset.py` | Pre-flight the self-study corpus before a fine-tune |
| `python3 tools/train/finetune_local.py --dry-run` | Validate + write a Modelfile without training (no GPU needed) |
| `scripts/clear_pyrevit_cache.ps1` | Clear pyRevit compiled cache |
| `scripts/fix_pyrevit_reload.ps1` | Fix pyRevit reload issues |

The 22 suites in `dev/` are plain-Python and cover the Intelligence layer —
routing, language, graph agents, knowledge, learning/dataset, exemplars, MCP
teaching, memory, performance and UI. They import the `lib/` packages directly,
so they run under CPython 3 outside Revit.

### Documentation

| Doc | Topic |
|-----|-------|
| [`docs/assistant-graph-architecture.md`](docs/assistant-graph-architecture.md) | Graph agent layer + bilingual VI/EN language layer |
| [`docs/assistant-performance.md`](docs/assistant-performance.md) | Knowledge, skills, latency and prompt-cache work |
| [`docs/assistant-revit-ui.md`](docs/assistant-revit-ui.md) | Why the Assistant follows Revit's theme instead of Lumina |
| [`docs/assistant-llms-setting-flow.md`](docs/assistant-llms-setting-flow.md) | Provider / model / API-key setting flow |
| [`docs/assistant-skills-from-github.md`](docs/assistant-skills-from-github.md) | Installing Claude-format skills from a repo link |
| [`docs/api-learning-guide.md`](docs/api-learning-guide.md) | Revit API self-learning layer |
| [`docs/cloud-family-loader.md`](docs/cloud-family-loader.md) | Cloud family metadata API |
| [`tools/train/README.md`](tools/train/README.md) | Local LoRA fine-tune + portable exemplars |

- UI design standard: `.claude/rules/ui-design-standard.md` (T3Lab Lumina — deep slate `#0F172A` + accent blue `#3B82F6`)
- Contributor / agent guide: [`AGENTS.md`](AGENTS.md) · Design notes: [`DESIGN.md`](DESIGN.md)
- Debug & QA plans: `dev/plan/`

---

## License

Released under the [MIT License](LICENSE) — © 2026 Tran Tien Thanh (T3Lab).

Autodesk® and Revit® are registered trademarks of Autodesk, Inc. This project is not affiliated with or endorsed by Autodesk.

---

## Author
**Tran Tien Thanh** — Architect & BIM Developer
- [trantienthanh909@gmail.com](mailto:trantienthanh909@gmail.com)
- [T3Lab.Space](https://t3lab.space)

---
*Empowering BIM with Intelligence.*
