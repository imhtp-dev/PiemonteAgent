"""
Call Retry Service
Monitors backup files and retries failed database saves
Sends email alerts on permanent failures
"""

import os
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from services.database import db
from utils.tracing import trace_api_call, add_span_attributes


class CallRetryService:
    """
    Service to retry failed call data saves
    - Checks backup directory every 5 minutes
    - Retries ONCE after initial failure
    - Sends email alert on permanent failure
    """

    def __init__(self):
        self.backup_dir = Path("info_agent/call_logs/failed_saves")
        self.check_interval = 300  # 5 minutes in seconds
        self.is_running = False
        self.task = None

        # Email configuration
        self.sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
        self.alert_from_email = os.getenv("ALERT_FROM_EMAIL", "alerts@cerbacare.it")
        self.alert_to_emails = os.getenv("ALERT_TO_EMAILS", "").split(",")  # Comma-separated

        logger.info("📧 Call Retry Service initialized")
        logger.info(f"   Backup directory: {self.backup_dir}")
        logger.info(f"   Check interval: {self.check_interval}s (5 minutes)")
        logger.info(f"   SendGrid configured: {bool(self.sendgrid_api_key)}")
        logger.info(f"   Alert emails: {len([e for e in self.alert_to_emails if e])}")

    async def start(self):
        """Start the retry service"""
        if self.is_running:
            logger.warning("⚠️ Retry service already running")
            return

        self.is_running = True
        self.task = asyncio.create_task(self._retry_loop())
        logger.success("✅ Call Retry Service started")

    async def stop(self):
        """Stop the retry service"""
        if not self.is_running:
            return

        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        logger.info("🛑 Call Retry Service stopped")

    async def _retry_loop(self):
        """Main loop to check and retry failed saves"""
        logger.info("🔄 Starting retry loop...")

        while self.is_running:
            try:
                await self._check_and_retry_failed_saves()
            except Exception as e:
                logger.error(f"❌ Error in retry loop: {e}")
                import traceback
                traceback.print_exc()

            # Wait 5 minutes before next check
            await asyncio.sleep(self.check_interval)

    async def _check_and_retry_failed_saves(self):
        """Check backup directory and retry failed saves"""
        if not self.backup_dir.exists():
            logger.debug("📁 No backup directory found, skipping check")
            return

        backup_files = list(self.backup_dir.glob("*.json"))

        if not backup_files:
            logger.debug("📁 No backup files found")
            return

        logger.info(f"📂 Found {len(backup_files)} backup file(s) to check")

        for backup_file in backup_files:
            try:
                await self._process_backup_file(backup_file)
            except Exception as e:
                logger.error(f"❌ Error processing backup file {backup_file.name}: {e}")
                import traceback
                traceback.print_exc()

    async def _process_backup_file(self, backup_file: Path):
        """Process a single backup file"""
        logger.info(f"🔍 Processing backup: {backup_file.name}")

        # Read backup data
        with open(backup_file, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        call_id = backup_data.get("call_id")
        retry_count = backup_data.get("retry_count", 0)
        saved_at = backup_data.get("saved_at")
        data = backup_data.get("data", {})

        logger.info(f"   Call ID: {call_id}")
        logger.info(f"   Retry count: {retry_count}")
        logger.info(f"   Saved at: {saved_at}")

        # Check if already retried once
        if retry_count >= 1:
            logger.warning(f"⚠️ Already retried once, sending permanent failure alert")
            await self._send_failure_alert(call_id, data, backup_file)
            # Delete backup file after sending alert
            backup_file.unlink()
            logger.info(f"🗑️ Deleted backup file after alert: {backup_file.name}")
            return

        # Check if saved more than 5 minutes ago
        saved_time = datetime.fromisoformat(saved_at)
        now = datetime.now()
        time_diff = (now - saved_time).total_seconds()

        if time_diff < 300:  # Less than 5 minutes
            logger.debug(f"⏱️ Backup too recent ({time_diff:.0f}s), waiting...")
            return

        # Retry save to database
        logger.info(f"🔄 Attempting retry for call {call_id}...")
        success = await self._retry_database_save(data)

        if success:
            logger.success(f"✅ Retry successful for call {call_id}")
            # Delete backup file
            backup_file.unlink()
            logger.info(f"🗑️ Deleted backup file: {backup_file.name}")
        else:
            logger.error(f"❌ Retry failed for call {call_id}")
            # Update retry count
            backup_data["retry_count"] = retry_count + 1
            backup_data["last_retry_at"] = datetime.now().isoformat()

            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)

            logger.warning(f"⚠️ Updated retry count to {retry_count + 1}")

    async def _retry_database_save(self, data: Dict[str, Any]) -> bool:
        """
        Retry saving call data to database

        Args:
            data: Call data to save

        Returns:
            True if successful, False otherwise
        """
        try:
            query = """
            UPDATE tb_stat SET
                phone_number = $2,
                assistant_id = $3,
                started_at = $4,
                ended_at = $5,
                duration_seconds = $6,
                action = $7,
                sentiment = $8,
                esito_chiamata = $9,
                motivazione = $10,
                patient_intent = $11,
                transcript = $12,
                summary = $13,
                cost = $14,
                llm_token = $15,
                service = $16,
                updated_at = CURRENT_TIMESTAMP
            WHERE call_id = $1
            """

            # Parse timestamps
            started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ended_at = datetime.fromisoformat(data["ended_at"]) if data.get("ended_at") else None

            result = await db.execute(
                query,
                data["call_id"],
                data.get("phone_number"),
                data.get("assistant_id"),
                started_at,
                ended_at,
                data.get("duration_seconds"),
                data.get("action"),
                data.get("sentiment"),
                data.get("esito_chiamata"),
                data.get("motivazione"),
                data.get("patient_intent"),
                data.get("transcript"),
                data.get("summary"),
                data.get("cost"),
                data.get("llm_token"),
                data.get("service")
            )

            logger.info(f"   Database rows updated: {result}")
            return True

        except Exception as e:
            logger.error(f"❌ Database retry failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    @trace_api_call("api.sendgrid_alert_email")
    async def _send_failure_alert(self, call_id: str, data: Dict[str, Any], backup_file: Path):
        """
        Send email alert for permanent failure

        Args:
            call_id: Call ID
            data: Call data
            backup_file: Path to backup file
        """
        # Add span attributes for tracking
        add_span_attributes({
            "alert.call_id": call_id,
            "alert.type": "permanent_failure",
            "alert.recipient_count": len([e for e in self.alert_to_emails if e]),
            "alert.phone_number": data.get("phone_number", "N/A"),
            "alert.duration_seconds": data.get("duration_seconds", 0),
            "alert.action": data.get("action", "N/A"),
            "alert.sentiment": data.get("sentiment", "N/A")
        })

        try:
            if not self.sendgrid_api_key:
                logger.error("❌ SendGrid API key not configured, cannot send alert")
                return

            if not any(self.alert_to_emails):
                logger.error("❌ No alert email addresses configured")
                return

            # Build email content
            subject = f"🚨 Permanent Call Data Save Failure - {call_id}"

            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #d32f2f;">⚠️ Permanent Call Data Save Failure</h2>

                <p>Failed to save call data after initial attempt and 1 retry (5-minute delay).</p>

                <h3>Call Details:</h3>
                <ul>
                    <li><strong>Call ID:</strong> {call_id}</li>
                    <li><strong>Phone Number:</strong> {data.get('phone_number', 'N/A')}</li>
                    <li><strong>Assistant ID:</strong> {data.get('assistant_id', 'N/A')}</li>
                    <li><strong>Started At:</strong> {data.get('started_at', 'N/A')}</li>
                    <li><strong>Ended At:</strong> {data.get('ended_at', 'N/A')}</li>
                    <li><strong>Duration:</strong> {data.get('duration_seconds', 'N/A')}s</li>
                    <li><strong>Action:</strong> {data.get('action', 'N/A')}</li>
                    <li><strong>Sentiment:</strong> {data.get('sentiment', 'N/A')}</li>
                    <li><strong>Service:</strong> {data.get('service', 'N/A')}</li>
                </ul>

                <h3>Next Steps:</h3>
                <ol>
                    <li>Check database connection and health</li>
                    <li>Review backup file at: <code>{backup_file}</code></li>
                    <li>Manually verify data integrity</li>
                    <li>Consider database recovery if multiple failures</li>
                </ol>

                <p style="margin-top: 30px; color: #666;">
                    <em>This is an automated alert from the Pipecat Info Agent Call Retry Service.</em>
                </p>
            </body>
            </html>
            """

            # Send to all configured emails
            for to_email in self.alert_to_emails:
                if not to_email:
                    continue

                message = Mail(
                    from_email=self.alert_from_email,
                    to_emails=to_email,
                    subject=subject,
                    html_content=html_content
                )

                sg = SendGridAPIClient(self.sendgrid_api_key)
                response = sg.send(message)

                logger.info(f"📧 Alert email sent to {to_email} (status: {response.status_code})")

            logger.success(f"✅ Failure alert sent for call {call_id}")

        except Exception as e:
            logger.error(f"❌ Failed to send alert email: {e}")
            import traceback
            traceback.print_exc()


# Global instance
_retry_service: Optional[CallRetryService] = None


def get_retry_service() -> CallRetryService:
    """Get or create global retry service instance"""
    global _retry_service
    if _retry_service is None:
        _retry_service = CallRetryService()
    return _retry_service


async def start_retry_service():
    """Start the global retry service"""
    service = get_retry_service()
    await service.start()


async def stop_retry_service():
    """Stop the global retry service"""
    service = get_retry_service()
    await service.stop()
