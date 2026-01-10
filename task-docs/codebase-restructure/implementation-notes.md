# Codebase Restructure - Analysis

## Current Structure Analysis

### Root Directory (Cluttered - 15+ files)
```
./bot.py                    # Main production agent
./test.py                   # Voice testing
./chat_test.py              # Text testing
./chat_service.py           # Chat API service
./talkdesk_hangup.py        # Utility script
./test_booking_api.py       # Test script
./test_langfuse_connection.py # Test script
./primer.md                 # ?
./agent_routing_flow.md     # Documentation
./CHAT_SERVICE_INTEGRATION.md
./DEPLOYMENT.md
./DEPLOYMENT_GUIDE.md       # Duplicate?
./CLAUDE.md
./setting.local.json        # Typo: should be settings
./deploy.sh
./rollback.sh
```

### Issues Identified

#### 1. Dead/Orphaned Code - `info_agent/`
The entire `info_agent/` folder is now DEAD after global functions migration:
- `info_agent/flows/` - No longer used (replaced by global_functions)
- `info_agent/services/` - Already moved to main `services/`
- `info_agent/config/` - Already moved to `config/info_settings.py`
- `info_agent/utils/` - Already moved to main `utils/`
- **STILL IN USE**: `info_agent/api/chat.py` and `info_agent/api/qa.py` (registered in bot.py)

#### 2. Duplicate/Scattered Folders
- `BridgeLombardia-main/` - Talkdesk bridge, should be separate repo or `bridge/`
- `extra/` - Old JSON files, purpose unclear
- `test_traces.json/` - Test data, should be in `tests/` or deleted
- `load_test/` - Should be in `tests/load/`

#### 3. Inconsistent Naming
- `setting.local.json` vs `settings.py` (typo)
- Multiple deployment docs: DEPLOYMENT.md, DEPLOYMENT_GUIDE.md
- `services/amb_json_flow_eng.py` - Cryptic name

#### 4. No Clear Entry Points
New developer doesn't know:
- Which file to run for production?
- Which file to run for testing?
- What's the main entry point?

#### 5. Missing Standard Folders
- No `tests/` folder for test files
- No `scripts/` folder for utilities
- No `api/` folder (scattered in info_agent/api/)

## File-by-File Analysis

### Files to DELETE (dead code)
```
info_agent/flows/              # Replaced by global_functions
info_agent/services/           # Moved to services/
info_agent/config/             # Moved to config/
info_agent/utils/              # Moved to utils/
info_agent/chat_test.py        # Duplicate of main chat_test.py
info_agent/main.py             # Not used
info_agent/create_admin_user.py # One-time script
info_agent/pipeline/           # Empty
info_agent/call_logs/          # Old logs
extra/                         # Old JSON files
test_traces.json/              # Test artifacts
setting.local.json             # Typo, duplicate of .claude/settings.local.json
```

### Files to MOVE
```
# Test files → tests/
test_booking_api.py           → tests/test_booking_api.py
test_langfuse_connection.py   → tests/test_langfuse_connection.py
load_test/                    → tests/load/

# Scripts → scripts/
deploy.sh                     → scripts/deploy.sh
rollback.sh                   → scripts/rollback.sh
talkdesk_hangup.py            → scripts/talkdesk_hangup.py

# API endpoints → api/
info_agent/api/chat.py        → api/chat.py
info_agent/api/qa.py          → api/qa.py

# Bridge → separate or bridge/
BridgeLombardia-main/         → Keep separate or move to bridge/
```

### Files to RENAME
```
services/amb_json_flow_eng.py  → services/flow_json_service.py (or delete if unused)
services/get_flowNb.py         → services/flow_number_service.py
services/slotAgenda.py         → services/slot_agenda.py (snake_case)
DEPLOYMENT_GUIDE.md            → Delete (duplicate of DEPLOYMENT.md)
```

## Proposed New Structure

```
pipecat-flows-italian/
├── README.md                    # Project overview (rename from CLAUDE.md?)
├── DEPLOYMENT.md                # Deployment guide
├── .env                         # Environment variables
├── requirements.txt             # Dependencies
├── Dockerfile
├── docker-compose.yml
│
├── bot.py                       # Production entry point
├── test.py                      # Voice testing entry point
├── chat_test.py                 # Text testing entry point
│
├── api/                         # HTTP API endpoints
│   ├── __init__.py
│   ├── chat.py                  # Chat endpoint (from info_agent)
│   └── qa.py                    # QA/Pinecone init
│
├── config/                      # Configuration
│   ├── __init__.py
│   ├── settings.py              # Main settings
│   ├── info_settings.py         # Info agent settings
│   └── telemetry.py             # Telemetry config
│
├── flows/                       # Conversation flows (pipecat-flows)
│   ├── __init__.py
│   ├── manager.py               # FlowManager creation
│   ├── global_functions.py      # Global function schemas
│   ├── handlers/                # Flow handlers
│   │   ├── __init__.py
│   │   ├── global_handlers.py
│   │   ├── service_handlers.py
│   │   ├── patient_handlers.py
│   │   ├── patient_detail_handlers.py
│   │   ├── patient_summary_handlers.py
│   │   ├── booking_handlers.py
│   │   ├── flow_handlers.py
│   │   └── agent_routing_handlers.py
│   └── nodes/                   # Flow node definitions
│       ├── __init__.py
│       ├── router.py
│       ├── greeting.py
│       ├── service_selection.py
│       ├── patient_info.py
│       ├── patient_details.py
│       ├── patient_summary.py
│       ├── booking.py
│       ├── booking_completion.py
│       ├── completion.py
│       └── transfer.py
│
├── services/                    # Business logic & external APIs
│   ├── __init__.py
│   ├── booking_api.py           # Booking API calls
│   ├── cerba_api.py             # Cerba API client
│   ├── fuzzy_search.py          # Service search
│   ├── sorting_api.py           # Sorting API
│   ├── patient_lookup.py        # Patient DB lookup
│   ├── knowledge_base.py        # KB queries
│   ├── pricing_service.py       # Pricing info
│   ├── exam_service.py          # Exam info
│   ├── clinic_info_service.py   # Clinic info
│   ├── slot_agenda.py           # Slot availability (renamed)
│   ├── flow_json_service.py     # Flow JSON (renamed)
│   ├── flow_number_service.py   # Flow number (renamed)
│   ├── escalation_service.py    # Transfer escalation
│   ├── call_data_extractor.py   # Call analytics
│   ├── call_logger.py           # Call logging
│   ├── call_storage.py          # Call data storage
│   ├── call_retry_service.py    # Retry logic
│   ├── transcript_manager.py    # Transcript handling
│   ├── processing_time_tracker.py
│   ├── idle_handler.py          # Idle detection
│   ├── llm_interpretation.py    # LLM helpers
│   ├── local_data_service.py    # Local data
│   ├── database.py              # DB connection
│   ├── auth.py                  # Authentication
│   ├── config.py                # Service config
│   └── timezone_utils.py        # Timezone helpers
│
├── pipeline/                    # Pipecat pipeline setup
│   ├── __init__.py
│   ├── components.py            # STT, TTS, LLM creation
│   ├── setup.py                 # Pipeline assembly
│   └── recording.py             # Recording handler
│
├── models/                      # Request/response models
│   ├── __init__.py
│   ├── requests.py
│   └── responses.py
│
├── utils/                       # Utilities
│   ├── __init__.py
│   ├── cache.py
│   ├── logging.py
│   ├── stt_switcher.py
│   └── tracing.py
│
├── data/                        # Static data files
│   └── all_services.json
│
├── scripts/                     # Utility scripts
│   ├── deploy.sh
│   ├── rollback.sh
│   └── talkdesk_hangup.py
│
├── tests/                       # Test files
│   ├── __init__.py
│   ├── test_booking_api.py
│   ├── test_langfuse_connection.py
│   └── load/
│       ├── load_tester.py
│       ├── monitor.sh
│       └── README.md
│
├── docs/                        # Documentation & learnings
│   ├── pipecat/
│   ├── cerba-api/
│   ├── talkdesk/
│   ├── voice-ai-patterns/
│   └── ...
│
├── task-docs/                   # Task planning (temporary)
│   └── ...
│
├── call_logs/                   # Runtime logs (gitignored)
├── logs/                        # Runtime logs (gitignored)
│
└── .claude/                     # Claude Code config
    ├── agents/
    └── commands/
```

## Best Practices Applied

### 1. Single Responsibility
- Each folder has ONE clear purpose
- `api/` = HTTP endpoints
- `services/` = Business logic
- `flows/` = Conversation state management

### 2. Clear Entry Points
- `bot.py` = Production (documented in README)
- `test.py` = Voice testing
- `chat_test.py` = Text testing

### 3. Consistent Naming
- All snake_case for Python files
- Descriptive names (no abbreviations like `amb`)

### 4. Separation of Concerns
- `tests/` separate from source
- `scripts/` separate from main code
- `data/` for static files

### 5. Standard Python Project Layout
- Follows common patterns (api/, services/, utils/, tests/)
- Easy for new developers to navigate

## Import Changes Required

After restructure, these imports need updating:
```python
# Old
from info_agent.api.chat import ...
from info_agent.api.qa import ...

# New
from api.chat import ...
from api.qa import ...
```

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Delete info_agent/ | Low | Already migrated, just verify imports |
| Move test files | Low | No runtime impact |
| Rename services | Medium | Grep for all imports before renaming |
| Move api/ | Medium | Update bot.py imports |
