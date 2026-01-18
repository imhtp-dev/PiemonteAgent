from pipecat.pipeline.task import PipelineTask
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.daily.transport import DailyTransport

from flows.tracked_flow_manager import TrackedFlowManager
from flows.nodes.greeting import create_greeting_node
from flows.global_functions import GLOBAL_FUNCTIONS


def create_flow_manager(
    task: PipelineTask,
    llm: OpenAILLMService,
    context_aggregator: LLMContextAggregatorPair,
    transport: DailyTransport
) -> TrackedFlowManager:
    """Create and initialize TrackedFlowManager with global functions.

    Uses TrackedFlowManager instead of FlowManager to enable automatic
    failure tracking. Transfers to human operator after:
    - 1 failure if knowledge gap detected
    - 1 failure if user requested transfer
    - 3 failures for normal technical issues
    """

    # Initialize TrackedFlowManager with global functions available at every node
    flow_manager = TrackedFlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        transport=transport,
        global_functions=GLOBAL_FUNCTIONS,  # 8 global functions (info, transfer, booking)
    )

    return flow_manager


async def initialize_flow_manager(flow_manager: TrackedFlowManager, start_node: str = "router") -> None:
    """Initialize flow manager with specified starting node"""
    if start_node == "router":
        # NEW: Start with unified router node (default)
        from flows.nodes.router import create_router_node
        await flow_manager.initialize(create_router_node())
    elif start_node == "greeting":
        # Direct to booking greeting (for testing/debugging)
        await flow_manager.initialize(create_greeting_node())
    elif start_node == "email":
        # Create a special entry node that switches STT then goes to email collection
        from pipecat_flows import NodeConfig, FlowsFunctionSchema
        from flows.handlers.patient_detail_handlers import start_email_collection_with_stt_switch

        email_entry_node = NodeConfig(
            name="email_entry",
            role_messages=[{
                "role": "system",
                "content": "Switching to high-accuracy transcription for email collection."
            }],
            task_messages=[{
                "role": "system",
                "content": "I'm switching to high-accuracy mode for email collection. Please wait a moment."
            }],
            functions=[
                FlowsFunctionSchema(
                    name="start_email_collection",
                    handler=start_email_collection_with_stt_switch,
                    description="Initialize email collection with enhanced transcription",
                    properties={},
                    required=[]
                )
            ]
        )
        await flow_manager.initialize(email_entry_node)
    elif start_node == "phone":
        from flows.nodes.patient_details import create_collect_phone_node
        await flow_manager.initialize(create_collect_phone_node())
    elif start_node == "name":
        from flows.nodes.patient_details import create_collect_name_node
        await flow_manager.initialize(create_collect_name_node())
    elif start_node == "fiscal_code":
        from flows.nodes.patient_details import create_collect_fiscal_code_node
        await flow_manager.initialize(create_collect_fiscal_code_node())
    elif start_node == "slot_selection":
        from flows.nodes.booking import create_collect_datetime_node
        from models.requests import HealthService, HealthCenter

        # Pre-populate state with data from logs for testing
        # This simulates having gone through: service selection, center selection, patient info, etc.
        service = HealthService(
            uuid="9a93d65f-396a-45e4-9284-94481bdd2b51",
            name="RX Caviglia Destra ",
            code="RRAD0019",
            synonyms=["Esame Radiografico Caviglia Destra","Esame Radiografico Caviglia dx","Lastra Caviglia Destra","Radiografia Caviglia Destra","Radiografia Caviglia dx","Radiografia della Caviglia Destra","Raggi Caviglia Destra","Raggi Caviglia dx","Raggi x Caviglia Destra","Raggi x Caviglia dx","RX Caviglia dx","RX della Caviglia Destra"],
            sector="health_services"
        )

        flow_manager.state.update({
            # Service selection data (from logs) - booking logic expects PLURAL
            "selected_service": service,  # Keep singular for compatibility
            "selected_services": [service],  # Add plural for booking logic
            # Center selection data (from logs)
            "selected_center": HealthCenter(
                uuid="6cff89d8-1f40-4eb8-bed7-f36e94a3355c",
                name="Rozzano Viale Toscana 35/37 - Delta Medica",
                address="Viale Toscana 35/37",
                city="Rozzano",
                district="Milano",
                phone="+39 02 1234567",
                region="Lombardia"
            ),
            # Patient data (from logs)
            "patient_data": {
                "gender": "m",
                "date_of_birth": "2007-04-27",
                "address": "Milan",
                "birth_city": "Milan"
            },
            # Cerba membership (from logs)
            "is_cerba_member": False
        })

        print("🧪 DATE SELECTION TEST MODE")
        print("=" * 50)
        print("📋 Pre-populated test data:")
        print(f"   Service: {flow_manager.state['selected_service'].name}")
        print(f"   Services (array): {[s.name for s in flow_manager.state['selected_services']]}")
        print(f"   Center: {flow_manager.state['selected_center'].name}")
        print(f"   Patient: Male, DOB: {flow_manager.state['patient_data']['date_of_birth']}")
        print("   📅 Starting from: Date selection")
        print("=" * 50)

        # Initialize with date collection node - user will be asked for preferred date/time
        await flow_manager.initialize(create_collect_datetime_node())
    elif start_node == "booking":
        # Start at booking flow - with pre-filled test data for faster testing
        from flows.nodes.booking import create_collect_datetime_node
        from models.requests import HealthService, HealthCenter

        # Pre-populate state with test service and center data
        service = HealthService(
            uuid="9a93d65f-396a-45e4-9284-94481bdd2b51",
            name="RX Caviglia Destra",
            code="RRAD0019",
            synonyms=["Esame Radiografico Caviglia Destra", "Esame Radiografico Caviglia dx", "Lastra Caviglia Destra", "Radiografia Caviglia Destra", "Radiografia Caviglia dx", "Radiografia della Caviglia Destra", "Raggi Caviglia Destra", "Raggi Caviglia dx", "Raggi x Caviglia Destra", "Raggi x Caviglia dx", "RX Caviglia dx", "RX della Caviglia Destra"],
            sector="health_services"
        )

        flow_manager.state.update({
            # Service selection data - booking logic expects PLURAL
            "selected_service": service,
            "selected_services": [service],
            # Center selection data
            "selected_center": HealthCenter(
                uuid="6cff89d8-1f40-4eb8-bed7-f36e94a3355c",
                name="Rozzano Viale Toscana 35/37 - Delta Medica",
                address="Viale Toscana 35/37",
                city="Rozzano",
                district="Milano",
                phone="+39 02 1234567",
                region="Lombardia"
            ),
            # Cerba membership
            "is_cerba_member": False,
            # Patient data will be populated from --caller-phone and --patient-dob if provided
            "current_service_index": 0
        })

        print("🧪 BOOKING TEST MODE")
        print("=" * 50)
        print("📋 Pre-populated test data:")
        print(f"   Service: {service.name}")
        print(f"   Center: {flow_manager.state['selected_center'].name}")
        if flow_manager.state.get("caller_phone_from_talkdesk"):
            print(f"   📞 Caller phone: {flow_manager.state.get('caller_phone_from_talkdesk')}")
        if flow_manager.state.get("patient_dob"):
            print(f"   📅 Patient DOB: {flow_manager.state.get('patient_dob')}")
        print("   📅 Starting from: Date/time selection")
        print("=" * 50)

        # Initialize with date collection node - user will be asked for preferred date/time
        await flow_manager.initialize(create_collect_datetime_node())
    elif start_node == "cerba_card":
        from flows.nodes.booking import create_cerba_membership_node
        from models.requests import HealthService, HealthCenter

        # Pre-populate state with test data up to the point where Cerba Card question is asked
        service1 = HealthService(
            uuid="9a93d65f-396a-45e4-9284-94481bdd2b51",  # RX Caviglia Destra
            name="RX Caviglia Destra ",
            code="RRAD0019",
            synonyms=[],
            sector="health_services"
        )
        service2 = HealthService(
            uuid="1cc793b7-4a8b-4c54-ac09-3c7ca7e5a168",  # Visita Ortopedica
            name="Visita Ortopedica (Prima Visita)",
            code="PORT0001",
            synonyms=[],
            sector="prescriptions"  # Since user said "no" to prescription, this will be prescriptions sector
        )

        flow_manager.state.update({
            # Multi-service booking data
            "selected_services": [service1, service2],
            "service_groups": [
                {"services": [service2], "is_group": False},  # First group: Visita Ortopedica
                {"services": [service1], "is_group": False}   # Second group: RX Caviglia
            ],
            "booking_scenario": "separate",
            "current_group_index": 0,

            # Center selection data
            "selected_center": HealthCenter(
                uuid="c5535638-6c18-444c-955d-89139d8276be",  # Cologno Monzese
                name="Cologno Monzese Viale Liguria 37 - Curie",
                address="Viale Liguria 37",
                city="Cologno Monzese",
                district="Milano",
                phone="+39 02 1234567",
                region="Lombardia"
            ),

            # Patient data
            "patient_data": {
                "gender": "m",
                "date_of_birth": "1989-04-29",  # 29 April 1989
                "address": "Milan",
                "birth_city": "Milan"
            },

            # Flow completion flags
            "center_selected": True,
            "sorting_api_success": True,
            "services_finalized": True
        })

        # Initialize at Cerba Card question
        await flow_manager.initialize(create_cerba_membership_node())
    elif start_node == "orange_box":
        from flows.nodes.booking import create_orange_box_node
        from models.requests import HealthService

        # Pre-populate state with RX Caviglia Destra service only
        service = HealthService(
            uuid="b5928e02-99c5-45ab-aa6c-51e57fca3fd2",
            name="Visita Cardiologica (Prima Visita)",
            code="PCAR0001",
            synonyms=["Prima Visita Cardiologica","Visita per Aritmie"],
            sector="health_services"
        )

        flow_manager.state.update({
            # Service data - orange box will generate the decision flow
            "selected_service": service,
            "selected_services": [service],

            # Patient data required for flow generation (sorting API needs this)
            "patient_gender": "m",
            "patient_dob": "1989-04-29",
            "patient_address": "Milan"
        })

        print("🧪 ORANGE BOX FLOW TEST MODE")
        print("=" * 50)
        print("📋 Pre-populated test data:")
        print(f"   Service: {service.name}")
        print(f"   UUID: {service.uuid}")
        print(f"   Code: {service.code}")
        print(f"   Sector: {service.sector}")
        print(f"   Patient: Male, DOB: 1989-04-29, Address: Milan")
        print("   📦 Starting from: Orange Box (Flow Generation)")
        print("=" * 50)

        # Initialize at orange box node - will generate decision flow for the service
        await flow_manager.initialize(create_orange_box_node())
    else:
        # Default to greeting node
        await flow_manager.initialize(create_greeting_node())