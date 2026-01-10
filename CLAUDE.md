# WORKFLOW SYSTEM

## Sub-Agents Available
Located in `.claude/agents/`:
- **pipecat-expert** - Research Pipecat framework patterns in `_refs/pipecat/`
- **booking-flow-expert** - Research this project's conversation flows

## Custom Commands Available
Located in `.claude/commands/`:
- **/plan-task** - Plan any feature before implementation (creates task-docs/)
- **/root-cause-analysis** - Collaborative debugging procedure
- **/save-learning** - Capture learnings to docs/ folder

## Knowledge Locations
- **Pipecat Source**: `_refs/pipecat/` - Actual framework source code
- **Pipecat Flows**: `_refs/pipecat-flows/` - Flows extension source
- **Project Learnings**: `docs/` - Accumulated knowledge by category
- **Task Planning**: `task-docs/` - Per-task planning documents

## Critical Workflow Rules

1. **Before implementing ANY significant feature:**
   - Use `/plan-task` command
   - Wait for user approval of the plan
   - Never jump straight to coding

2. **When researching Pipecat:**
   - ALWAYS search `_refs/pipecat/src/` for actual implementation
   - ALWAYS check `docs/pipecat/` for past learnings first
   - Cite file paths when referencing source code

3. **After solving any significant problem:**
   - Use `/save-learning` to document it
   - This builds the knowledge base for future tasks

4. **When debugging:**
   - Use `/root-cause-analysis` for systematic debugging
   - Document the fix in `docs/` when solved

---



# Healthcare Booking Agent - Pipecat Flows (Italian)

## Project Overview

You are expert in Python, Pipecat, Docker, Git, Github and Azure Deployment. You have 5+ years of experience in building AI voice agents in pipecat framework. This is a healthcare booking agent built on the Pipecat framework using pipecat flows for voice-based appointment booking in Italian healthcare facilities. The agent uses advanced conversation flows to help patients book medical services through natural voice interactions.

### Architecture Flow
```
Incoming Call (Talkdesk) → bridge_conn.py (Azure) → bot.py (Azure VM)
```

**Key Components:**
- **Production Agent**: `bot.py` - FastAPI WebSocket transport for Talkdesk integration
- **Voice Testing**: `voice_test.py` - Daily room creation for voice testing with STT/TTS
- **Text Testing**: `chat_test.py` - Fast text-only chat interface (no STT/TTS for rapid testing)
- **Conversation Flows**: `flows/` - Dynamic conversation management using pipecat-flows
- **API Endpoints**: `api/` - HTTP API endpoints (chat)

## Technology Stack

### Core Framework
- **Pipecat AI**: Open-source conversational AI framework with flows extension
- **Transport**: FastAPI WebSocket (production) / Daily WebRTC (testing)
- **Language**: Python 3.11+

### AI Services
- **STT**: Deepgram (nova-3-general model) - Italian (`it`) / English (`en`) for testing
- **LLM**: OpenAI GPT-4.1-mini with function calling for healthcare service matching
- **TTS**: ElevenLabs (eleven_multilingual_v2, voice_id: `gfKKsLN1k0oYYN9n2dXX`)
- **VAD**: Silero Voice Activity Detection

### Infrastructure
- **Production**: Azure VM with Docker deployment
- **Database**: Azure MySQL (voila_tech_voice) with 2-month data retention
- **Storage**: Azure Blob Storage, Redis cache
- **Container Registry**: Private Docker registry (`rudyimhtpdev/voicebooking_piemo1:latest`)

## Development Workflow

### Local Development & Testing

**Two Testing Modes Available:**

1. **Text Chat Testing (RECOMMENDED for rapid development)** - `chat_test.py`
   ```bash
   python chat_test.py                         # Full flow (greeting) - TEXT ONLY
   python chat_test.py --start-node email      # Start with email collection
   python chat_test.py --start-node booking    # Start with booking flow
   python chat_test.py --port 8081             # Custom port
   ```
   - **Benefits**: 10x faster testing, no STT/TTS costs, instant responses, better debugging
   - **Opens browser chat UI**: http://localhost:8081
   - **Use for**: Rapid flow development, debugging, feature testing
   - **See**: [README_CHAT_TESTING.md](README_CHAT_TESTING.md) for details

2. **Voice Testing** - `voice_test.py`
   ```bash
   python voice_test.py                              # Full flow (greeting) - WITH VOICE
   python voice_test.py --start-node email           # Start with email collection
   python voice_test.py --start-node booking         # Start with booking flow
   ```
   - **Benefits**: Real voice testing, STT/TTS validation, natural conversation
   - Creates Daily room automatically for voice testing
   - Uses same codebase as production (bot.py components)
   - **Use for**: Final validation before production, voice quality testing

3. **Recommended Workflow**
   - ✅ Use `chat_test.py` for rapid iteration and debugging (90% of development)
   - ✅ Use `voice_test.py` for final voice validation (10% of development)
   - ✅ All flows work identically in both modes

### Production Deployment Process
1. **Code Changes**: Make changes and test locally with `voice_test.py`
2. **Push to GitHub**: Commit changes to main branch
3. **Build Docker Image**: Build and push to private registry
4. **Deploy to Azure VM**:
   ```bash
   docker-compose pull
   docker-compose up -d
   docker image prune -f
   ```

### Language Configuration

**Global Language Control**: Use `{language}` variable in all prompts
- **Italian (Production)**: `"You must talk in Italian"`
- **English (Testing)**: `"You must talk in English"`
- **STT Language**: Change `config/settings.py` → `deepgram_config["language"]` (`"it"` / `"en"`)

## Code Conventions

### File Structure
```
├── bot.py                          # Main production agent (FastAPI WebSocket)
├── voice_test.py                   # Voice testing with Daily rooms (STT/TTS)
├── chat_test.py                    # Text-only testing (no STT/TTS) - FAST!
├── chat_service.py                 # Chat API for dashboard testing
│
├── api/                            # HTTP API endpoints
│   ├── chat.py                     # Chat endpoint for testing
│   └── qa.py                       # Pinecone/OpenAI initialization
│
├── flows/                          # Conversation flow management
│   ├── manager.py                  # Flow initialization and management
│   ├── global_functions.py         # Global function schemas (8 functions)
│   ├── handlers/                   # Flow event handlers
│   └── nodes/                      # Individual conversation nodes
│
├── services/                       # Business logic & external APIs
│   ├── booking_api.py              # Booking API calls
│   ├── knowledge_base.py           # KB queries
│   ├── pricing_service.py          # Pricing info
│   ├── talkdesk_service.py         # Talkdesk API integration
│   └── ...                         # Other services
│
├── config/                         # Configuration
│   └── settings.py                 # All settings (API keys, endpoints, timeouts)
│
├── pipeline/                       # Pipecat pipeline setup
│   ├── components.py               # STT, TTS, LLM creation
│   └── setup.py                    # Pipeline assembly
│
├── models/                         # Request/response models
├── utils/                          # Utilities (cache, logging, tracing)
│
├── tests/                          # Test files
│   ├── load/                       # Load testing
│   └── test_*.py                   # Unit tests
│
├── scripts/                        # Utility scripts
│   ├── deploy.sh                   # Docker build and push
│   └── rollback.sh                 # Docker rollback
│
├── docs/                           # Learnings and documentation
└── task-docs/                      # Task planning documents
```

### Healthcare Service Integration
- **Service Matching**: Patient requests → API call → fuzzy matching → top results → user selection
- **Sorting API**: Detects package deals from health centers ([services/sorting_api.py](services/sorting_api.py))
  - Organizes services by sector (health_services, prescriptions, preliminary_visits, optionals, opinions)
  - Compares requested vs returned UUIDs to detect package substitutions
  - Returns `package_detected` flag if center offers bundle deals
- **Flow Pattern**: Always use pipecat-flows for multi-turn conversations
- **Data Handling**: Store conversation data with 2-month retention policy

### Naming Conventions
- **Flow Nodes**: `create_{purpose}_node()` (e.g., `create_greeting_node()`)
- **Handlers**: `{action}_{entity}_handlers.py` (e.g., `patient_detail_handlers.py`)
- **Services**: `{service_name}_service()` (e.g., `create_stt_service()`)

### Configuration Management
- **Environment Variables**: All API keys in `.env` file
- **Settings**: Centralized in `config/settings.py`
- **Service Config**: Use `services/config.py` for healthcare API integration

## Development Commands

### Local Testing
```bash
# Start full conversation flow
python voice_test.py

# Test specific flow nodes (works with both voice_test.py and chat_test.py)
python voice_test.py --start-node greeting        # Full flow from start (default)
python voice_test.py --start-node email           # Email collection with STT switching
python voice_test.py --start-node name            # Name collection
python voice_test.py --start-node phone           # Phone number collection
python voice_test.py --start-node fiscal_code     # Italian tax code collection
python voice_test.py --start-node booking         # Pre-filled service/center, start at date selection
python voice_test.py --start-node slot_selection  # Advanced testing with full state
python voice_test.py --start-node cerba_card      # Cerba membership question

# Simulate existing patient (Talkdesk caller ID)
python voice_test.py --caller-phone +393491234567 --patient-dob 1979-06-19

# Debug mode
python voice_test.py --debug
```

### Docker Commands
```bash
# Build image locally
docker build -t healthcare-agent .

# Run locally
docker-compose up -d

# Check logs
docker-compose logs -f pipecat-agent

# Cleanup
docker image prune -f
```

### Production Deployment
```bash
# On Azure VM
docker-compose pull
docker-compose up -d
docker image prune -f

# Verify deployment
docker-compose ps
curl http://localhost:8000/health
docker-compose logs --tail=100 pipecat-agent
```

### Docker Container Verification
```bash
# Check if sorting_api.py exists in container
docker exec <container-name> ls -la /app/services/sorting_api.py

# Verify module can be imported
docker exec <container-name> python -c "from services.sorting_api import call_sorting_api; print('✓ Sorting API loaded')"

# List all services files
docker exec <container-name> ls -la /app/services/

# Check image creation date
docker inspect rudyimhtpdev/voicebooking_piemo1:latest | grep Created
```

## Required Environment Variables

### Core API Keys
```bash
DEEPGRAM_API_KEY=your_key_here          # STT service
ELEVENLABS_API_KEY=your_key_here        # TTS service
OPENAI_API_KEY=your_key_here            # LLM service
```

### Daily Testing (Local Development)
```bash
DAILY_API_KEY=your_key_here             # For voice_test.py Daily room creation
DAILY_API_URL=https://api.daily.co/v1   # Daily API endpoint
```

### Production Services
```bash
PIPECAT_SERVER_URL=ws://AzureIP:8000/ws   # Pipecat WebSocket URL
HOST=0.0.0.0                            # Server host
PORT=8000                               # Server port
```

### Talkdesk Integration
```bash
TALKDESK_CLIENT_ID=your_client_id       # Talkdesk OAuth client ID
TALKDESK_CLIENT_SECRET=your_secret      # Talkdesk OAuth client secret
TALKDESK_ACCOUNT_NAME=cerba             # Talkdesk account name (default: cerba)
TALKDESK_API_URL=https://api.talkdeskapp.eu/interaction-custom-fields
```

### Cerba Healthcare API
```bash
CERBA_CLIENT_ID=your_client_id          # Cerba Cognito client ID
CERBA_CLIENT_SECRET=your_secret         # Cerba Cognito client secret
CERBA_COGNITO_URL=https://cerbahc.auth.eu-central-1.amazoncognito.com/oauth2/token
```

## Flow Development Guidelines

### Adding New Conversation Flows
1. **Create Node**: `flows/nodes/{purpose}.py`
2. **Create Handler**: `flows/handlers/{purpose}_handlers.py`
3. **Register in Manager**: Update `flows/manager.py`
4. **Test Locally**: Use `voice_test.py --start-node {purpose}`

### Flow Best Practices
- **Use Function Calling**: For healthcare service integration and user data collection
- **State Management**: Leverage pipecat-flows for complex conversation states
- **Error Handling**: Always provide fallback flows for API failures
- **Language Variables**: Include `{language}` in all system prompts

### Critical Flow Patterns

#### State Management
```python
# Store data in flow_manager.state (accessible across all nodes)
flow_manager.state["selected_service"] = service
flow_manager.state["patient_data"] = {
    "name": "John Doe",
    "phone": "+39xxx",
    "email": "john@example.com"
}
```

#### Flow Transitions
**CRITICAL**: Handlers MUST always return a `NodeConfig` to transition flows:
```python
async def handler_function(flow_manager, args):
    # Process business logic
    result = await process_data(args)

    # Update state
    flow_manager.state["result"] = result

    # Return next node (MUST return NodeConfig)
    return create_next_node()
```

#### Caller Phone Handling (Talkdesk Integration)
```python
# Talkdesk passes caller phone via query param
caller_phone = query_params.get("caller_phone", "")

# Store in flow state for all nodes to access
flow_manager.state["caller_phone_from_talkdesk"] = caller_phone

# Use in patient lookup
if caller_phone:
    existing_patient = await lookup_patient_by_phone(caller_phone)
```

#### Dynamic STT Switching (Email Mode)
```python
# Switch to email mode (Nova-3 multi-language for better recognition)
from utils.stt_switcher import switch_to_email_transcription
await switch_to_email_transcription()

# Switch back to default (Nova-2 Italian)
from utils.stt_switcher import switch_to_default_transcription
await switch_to_default_transcription()
```


## Testing Strategy

### Local Testing with Daily Rooms
- **Purpose**: Full conversation flow testing before production
- **Benefits**: Real voice interaction, transcript recording, same codebase as production
- **Usage**: Always test new flows with `voice_test.py` before deployment

### Language Testing
1. **Switch to English**: Change `{language}` variable and STT language setting
2. **Test Flows**: Verify all conversation paths work in English
3. **Switch Back**: Return to Italian settings for production deployment

## Architecture Decisions

### Transport Strategy
- **Production**: FastAPI WebSocket for Talkdesk integration (stable, production-ready)
- **Testing**: Daily WebRTC for development (easier debugging, real voice testing)

### AI Service Selection
- **STT**: Deepgram for accuracy and multi-language support
- **LLM**: OpenAI GPT-4.1-mini for function calling capabilities
- **TTS**: ElevenLabs for natural Italian voice synthesis

### Data Management
- **Storage**: Azure services for production scalability
- **Retention**: 2-month automatic deletion for Italian compliance

## Troubleshooting

### Common Issues
1. **Audio Quality**: Check VAD settings in transport configuration
2. **Flow Transitions**: Verify function calling implementation in handlers
3. **Service Matching**: Test healthcare API connectivity and fuzzy matching logic
4. **Language Issues**: Confirm `{language}` variable and STT language settings match

### Debugging Tools
- **Local Testing**: `python voice_test.py --debug` for detailed logging
- **Production Logs**: `docker-compose logs -f pipecat-agent`
- **Health Check**: `/health` endpoint for service status

## Performance Optimizations

### Docker Multi-stage Build
- **Base Dependencies**: Cached layer for stable requirements
- **Development Dependencies**: Separate layer for faster rebuilds
- **PyTorch Models**: Pre-downloaded in build process

### Pipeline Configuration
- **Interruptions**: Enabled for natural conversation flow
- **VAD Tuning**: Optimized for Italian speech patterns
- **Memory Management**: Cleanup enabled for long-running sessions

---

**Solo Developer Notes**: This project is maintained by a single developer. All documentation assumes solo development workflow with focus on efficient testing and deployment procedures.
**Important** Pipecat is not vastly recognized so you might not have much data about it. So, it's good thing to search the relevant docs , github repos and communities of pipecat to get the verified and up to date data about it before generating any code and giving advice on pipecat
**Important** Never start writing code by yourself. First understand user problem what he is saying and then reason on that understand it and think logically. Provide with your thinking to user how we can solve that problem. Then if user verify your approach or suggest improvements improve it and then when user says to go with it and write code then start writing code. Never ever write written by claude code in commit messages