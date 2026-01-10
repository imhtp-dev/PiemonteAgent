# Codebase Structure Best Practices

## Problem
Codebase became cluttered with:
- Dead code from migrated features (info_agent/)
- Test files scattered in root
- Scripts mixed with source code
- Unclear entry points for new developers

## Solution

### Standard Python Project Layout
```
Root (entry points only):
├── bot.py              # Production
├── voice_test.py       # Voice testing
├── chat_test.py        # Text testing
├── chat_service.py     # Dashboard chat API

Organized by purpose:
├── api/                # HTTP endpoints
├── flows/              # Conversation flows (pipecat-flows)
│   ├── handlers/       # Flow event handlers
│   └── nodes/          # Node definitions
├── services/           # Business logic & external APIs
├── config/             # Configuration
├── pipeline/           # Pipecat pipeline setup
├── models/             # Request/response models
├── utils/              # Utilities
├── tests/              # Test files
│   └── load/           # Load testing
├── scripts/            # Deployment scripts (sh, ps1)
├── docs/               # Learnings
└── data/               # Static data files
```

### Naming Conventions
- Entry points: descriptive (`voice_test.py` not `test.py`)
- Handlers: `{purpose}_handlers.py`
- Nodes: `{purpose}.py` in `flows/nodes/`
- Services: `{purpose}_service.py` or `{purpose}.py`

### What Goes Where

| Type | Location |
|------|----------|
| HTTP endpoints | `api/` |
| Conversation flows | `flows/` |
| External API calls | `services/` |
| Pipecat STT/TTS/LLM | `pipeline/` |
| Test files | `tests/` |
| Shell/PowerShell scripts | `scripts/` |
| Static JSON data | `data/` |

## Key Code Reference
- `CLAUDE.md` - File structure documentation (lines 135-180)
- `scripts/` - Deployment scripts (deploy.sh, rollback.sh, *.ps1)

## Gotchas

1. **Dead code accumulates** - After migrations, delete old folders immediately
2. **Entry points in root only** - Keep root clean with only main executables
3. **Scripts vs Services** -
   - `scripts/` = CLI tools, deployment scripts (not imported)
   - `services/` = Python modules (imported by other code)
4. **PowerShell equivalents** - Keep `.ps1` versions of `.sh` scripts for Windows

## Cleanup Checklist
When migrating features:
- [ ] Move reusable code to new location
- [ ] Update all imports (`grep -r "from old_path"`)
- [ ] Delete old folder entirely
- [ ] Update CLAUDE.md file structure
- [ ] Run `python -m py_compile` on all files

## Date Learned
2024-12-24

## Related
- `docs/pipecat/global-functions-implementation.md` - Migration example
