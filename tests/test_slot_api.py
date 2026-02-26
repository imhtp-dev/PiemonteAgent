"""
Test script for Slot Search API

Endpoint: amb/health-center/{healthCenterUUID}/slot

Usage:
    conda run -n healthcare-agent python tests/test_slot_api.py
    conda run -n healthcare-agent python tests/test_slot_api.py --dry-run
    conda run -n healthcare-agent python tests/test_slot_api.py --center-uuid <uuid> --gender f --dob 19790619
    conda run -n healthcare-agent python tests/test_slot_api.py --date 2026-03-01
    conda run -n healthcare-agent python tests/test_slot_api.py --service-uuid <uuid1> --service-uuid <uuid2>
    conda run -n healthcare-agent python tests/test_slot_api.py --start-time "2026-02-19 09:00:00+00" --end-time "2026-02-19 17:00:00+00"
    conda run -n healthcare-agent python tests/test_slot_api.py --limit 5

    # Run preconfigured scenario from call log
    conda run -n healthcare-agent python tests/test_slot_api.py --scenario novara-ginecologica
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

logger.remove()
logger.add(sys.stdout, level="DEBUG", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")

# Defaults
DEFAULT_CENTER_UUID = "b6766932-8b4f-4ce3-a959-b1142e8daf11"
DEFAULT_SERVICE_UUIDS = ["0f1b2c75-e84b-432a-8f7e-d172dc8eae7a"]
DEFAULT_DATE = "2026-02-19"

# --- Preconfigured scenarios from call logs ---
SCENARIOS = {
    "novara-ginecologica": {
        "description": "Call 0edfe186 (2026-02-25): Visita Ginecologica + Pap Test at Novara, afternoon Mar 1",
        "center_uuid": "b6766932-8b4f-4ce3-a959-b1142e8daf11",  # Novara Viale Dante Alighieri 43A
        "service_uuids": ["8086ff9b-266e-434f-aa5f-60fc5edfdc34"],  # Combined: Visita Ginecologica + Pap Test
        "gender": "f",
        "dob": "19790619",
        # Test multiple time windows to find where slots actually exist
        "searches": [
            {"label": "Mar 1 afternoon (original call)", "date": "2026-03-01", "start": "2026-03-01 12:00:00+00", "end": "2026-03-01 19:00:00+00"},
            {"label": "Mar 1 morning", "date": "2026-03-01", "start": "2026-03-01 08:00:00+00", "end": "2026-03-01 12:00:00+00"},
            {"label": "Mar 1 any time", "date": "2026-03-01", "start": None, "end": None},
            {"label": "Mar 3 any time", "date": "2026-03-03", "start": None, "end": None},
            {"label": "Mar 5 any time", "date": "2026-03-05", "start": None, "end": None},
            {"label": "Mar 10 any time", "date": "2026-03-10", "start": None, "end": None},
        ]
    },
    "novara-ginecologica-mar25": {
        "description": "Call (2026-02-25): Visita Ginecologica + Pap Test at Novara, Mar 25 - returned 0 slots",
        "center_uuid": "b6766932-8b4f-4ce3-a959-b1142e8daf11",  # Novara Viale Dante Alighieri 43A
        "service_uuids": ["8086ff9b-266e-434f-aa5f-60fc5edfdc34"],
        "gender": "f",
        "dob": "19800413",
        "searches": [
            {"label": "Mar 25 any time (original call)", "date": "2026-03-25", "start": None, "end": None},
            {"label": "Mar 26 any time", "date": "2026-03-26", "start": None, "end": None},
            {"label": "Mar 28 any time", "date": "2026-03-28", "start": None, "end": None},
            {"label": "Apr 1 any time", "date": "2026-04-01", "start": None, "end": None},
            {"label": "Apr 7 any time", "date": "2026-04-07", "start": None, "end": None},
        ]
    }
}


def run_single_search(center_uuid, date, service_uuids, gender, dob, start_time=None, end_time=None, limit=3):
    """Run a single slot search and return results"""
    from services.slotAgenda import list_slot

    result = list_slot(
        health_center_uuid=center_uuid,
        date_search=date,
        uuid_exam=service_uuids,
        gender=gender,
        date_of_birth=dob,
        start_time=start_time,
        end_time=end_time
    )
    return result


def run_scenario(name):
    """Run a preconfigured scenario with multiple searches"""
    scenario = SCENARIOS[name]

    logger.info("=" * 70)
    logger.info(f"📡 SCENARIO: {name}")
    logger.info(f"   {scenario['description']}")
    logger.info("=" * 70)
    logger.info(f"  Center: {scenario['center_uuid']}")
    logger.info(f"  Services: {scenario['service_uuids']}")
    logger.info(f"  Gender: {scenario['gender']} | DOB: {scenario['dob']}")
    logger.info("=" * 70)

    summary = []
    for search in scenario["searches"]:
        label = search["label"]
        logger.info(f"\n{'─'*60}")
        logger.info(f"🔍 {label}")
        logger.info(f"   Date: {search['date']} | Time: {search.get('start', 'any')} - {search.get('end', 'any')}")

        result = run_single_search(
            center_uuid=scenario["center_uuid"],
            date=search["date"],
            service_uuids=scenario["service_uuids"],
            gender=scenario["gender"],
            dob=scenario["dob"],
            start_time=search.get("start"),
            end_time=search.get("end"),
        )

        count = len(result) if result else 0
        summary.append((label, count))

        if result:
            logger.success(f"✅ {count} slots found")
            for slot in result[:3]:
                print(json.dumps(slot, indent=2, ensure_ascii=False))
        else:
            logger.warning(f"❌ No slots")

    # Print summary
    logger.info(f"\n{'='*70}")
    logger.info("📋 SUMMARY")
    logger.info("=" * 70)
    for label, count in summary:
        status = "✅" if count > 0 else "❌"
        logger.info(f"  {status} {label} → {count} slots")

    total = sum(c for _, c in summary)
    if total == 0:
        logger.error(f"  ⚠️  No slots found in ANY time window")
    logger.info("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Test Slot Search API")
    parser.add_argument("--center-uuid", default=DEFAULT_CENTER_UUID, help="Health center UUID")
    parser.add_argument("--gender", default="f", choices=["m", "f"], help="Patient gender")
    parser.add_argument("--dob", default="19790619", help="Date of birth YYYYMMDD")
    parser.add_argument("--date", default=DEFAULT_DATE, help="Start date YYYY-MM-DD")
    parser.add_argument("--start-time", default=None, help="Start time filter e.g. '2026-02-19 09:00:00+00'")
    parser.add_argument("--end-time", default=None, help="End time filter e.g. '2026-02-19 17:00:00+00'")
    parser.add_argument("--limit", type=int, default=3, help="Max availabilities to return")
    parser.add_argument("--dry-run", action="store_true", help="Show request params without calling API")
    parser.add_argument("--service-uuid", action="append", dest="service_uuids",
                        help="Service UUID (repeatable). Defaults to Ecografia Transvaginale.")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()),
                        help="Run preconfigured scenario from call logs")

    args = parser.parse_args()

    # Run scenario mode
    if args.scenario:
        run_scenario(args.scenario)
        return

    # Original single-search mode
    service_uuids = args.service_uuids or DEFAULT_SERVICE_UUIDS

    logger.info("=" * 70)
    logger.info("📡 SLOT SEARCH API TEST")
    logger.info("=" * 70)
    logger.info(f"  Center: {args.center_uuid}")
    logger.info(f"  Gender: {args.gender}")
    logger.info(f"  DOB: {args.dob}")
    logger.info(f"  Date: {args.date}")
    logger.info(f"  Start Time: {args.start_time or 'any'}")
    logger.info(f"  End Time: {args.end_time or 'any'}")
    logger.info(f"  Limit: {args.limit}")
    logger.info(f"  Services: {service_uuids}")
    logger.info("=" * 70)

    if args.dry_run:
        logger.warning("🔒 DRY RUN — not calling API")
        params = {
            "gender": args.gender,
            "date_of_birth": args.dob,
            "health_services": service_uuids,
            "start_date": args.date,
            "start_time": args.start_time,
            "end_time": args.end_time,
            "availabilities_limit": args.limit
        }
        print(json.dumps(params, indent=2, default=str))
        return

    from services.slotAgenda import list_slot

    result = list_slot(
        health_center_uuid=args.center_uuid,
        date_search=args.date,
        uuid_exam=service_uuids,
        gender=args.gender,
        date_of_birth=args.dob,
        start_time=args.start_time,
        end_time=args.end_time
    )

    logger.info("=" * 70)
    if result:
        logger.success(f"✅ {len(result)} slots found")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        logger.error("❌ No slots found or API error")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
