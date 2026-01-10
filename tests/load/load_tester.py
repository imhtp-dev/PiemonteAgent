#!/usr/bin/env python3
"""
Pipecat Voice Agent Load Tester
================================
Simulates concurrent WebSocket connections to test capacity.

Usage:
    python load_test/load_tester.py --calls 10 --duration 30
    python load_test/load_tester.py --calls 40 --duration 60 --ramp-up 20
"""

import asyncio
import websockets
import uuid
import time
import json
import argparse
import statistics
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class CallMetrics:
    """Metrics for a single simulated call"""
    call_id: str
    start_time: float
    connect_time: Optional[float] = None
    first_audio_time: Optional[float] = None
    end_time: Optional[float] = None
    bytes_sent: int = 0
    bytes_received: int = 0
    errors: List[str] = field(default_factory=list)
    status: str = "pending"  # pending, connected, completed, failed


@dataclass
class TestResults:
    """Aggregated test results"""
    total_calls: int
    successful_calls: int
    failed_calls: int
    avg_connect_time: float
    max_connect_time: float
    min_connect_time: float
    avg_first_audio_time: float
    total_bytes_sent: int
    total_bytes_received: int
    errors: List[str]
    duration_seconds: float
    calls_per_second: float


class LoadTester:
    def __init__(
        self,
        target_url: str,
        num_calls: int = 10,
        call_duration: int = 30,
        ramp_up_seconds: int = 10,
        audio_sample_rate: int = 16000,
        verbose: bool = False
    ):
        self.target_url = target_url
        self.num_calls = num_calls
        self.call_duration = call_duration
        self.ramp_up_seconds = ramp_up_seconds
        self.audio_sample_rate = audio_sample_rate
        self.verbose = verbose

        self.metrics: List[CallMetrics] = []
        self.active_calls = 0
        self.peak_concurrent = 0
        self.test_start_time = 0

        # Generate silence audio (20ms frames at 16kHz = 640 bytes)
        self.silence_frame = b'\x00' * 640

    def log(self, message: str, level: str = "INFO"):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = {"INFO": "ℹ️", "SUCCESS": "✅", "ERROR": "❌", "WARN": "⚠️"}.get(level, "")
        print(f"[{timestamp}] {prefix} {message}")

    async def simulate_call(self, call_index: int) -> CallMetrics:
        """Simulate a single voice call"""
        call_id = f"load-test-{uuid.uuid4().hex[:8]}"
        metrics = CallMetrics(call_id=call_id, start_time=time.time())

        try:
            # Build WebSocket URL with parameters
            ws_url = f"{self.target_url}?session_id={call_id}&business_status=open&caller_phone=%2B39test{call_index:04d}"

            if self.verbose:
                self.log(f"[{call_index}] Connecting: {call_id}")

            # Connect with timeout
            connect_start = time.time()
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20  # 1MB max message size
            ) as ws:
                metrics.connect_time = time.time() - connect_start
                metrics.status = "connected"
                self.active_calls += 1
                self.peak_concurrent = max(self.peak_concurrent, self.active_calls)

                if self.verbose:
                    self.log(f"[{call_index}] Connected in {metrics.connect_time:.2f}s", "SUCCESS")

                # Run call for specified duration
                end_time = time.time() + self.call_duration
                frame_interval = 0.02  # 20ms frames

                # Create tasks for sending and receiving
                send_task = asyncio.create_task(
                    self._send_audio(ws, metrics, end_time, frame_interval, call_index)
                )
                recv_task = asyncio.create_task(
                    self._receive_audio(ws, metrics, end_time, call_index)
                )

                # Wait for both tasks or timeout
                try:
                    await asyncio.wait_for(
                        asyncio.gather(send_task, recv_task),
                        timeout=self.call_duration + 10
                    )
                except asyncio.TimeoutError:
                    send_task.cancel()
                    recv_task.cancel()

                metrics.status = "completed"
                metrics.end_time = time.time()
                self.active_calls -= 1

                if self.verbose:
                    duration = metrics.end_time - metrics.start_time
                    self.log(f"[{call_index}] Completed: {duration:.1f}s, "
                            f"sent={metrics.bytes_sent/1024:.1f}KB, "
                            f"recv={metrics.bytes_received/1024:.1f}KB", "SUCCESS")

        except websockets.exceptions.ConnectionClosedError as e:
            metrics.status = "failed"
            metrics.errors.append(f"Connection closed: {e}")
            self.active_calls = max(0, self.active_calls - 1)
            self.log(f"[{call_index}] Connection closed: {e}", "ERROR")

        except asyncio.TimeoutError:
            metrics.status = "failed"
            metrics.errors.append("Connection timeout")
            self.active_calls = max(0, self.active_calls - 1)
            self.log(f"[{call_index}] Timeout", "ERROR")

        except Exception as e:
            metrics.status = "failed"
            metrics.errors.append(str(e))
            self.active_calls = max(0, self.active_calls - 1)
            self.log(f"[{call_index}] Error: {e}", "ERROR")

        return metrics

    async def _send_audio(
        self,
        ws: websockets.WebSocketClientProtocol,
        metrics: CallMetrics,
        end_time: float,
        frame_interval: float,
        call_index: int
    ):
        """Send silence audio frames"""
        try:
            while time.time() < end_time:
                await ws.send(self.silence_frame)
                metrics.bytes_sent += len(self.silence_frame)
                await asyncio.sleep(frame_interval)
        except Exception as e:
            if "closed" not in str(e).lower():
                metrics.errors.append(f"Send error: {e}")

    async def _receive_audio(
        self,
        ws: websockets.WebSocketClientProtocol,
        metrics: CallMetrics,
        end_time: float,
        call_index: int
    ):
        """Receive audio responses"""
        try:
            while time.time() < end_time:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    if isinstance(data, bytes):
                        metrics.bytes_received += len(data)
                        if metrics.first_audio_time is None and len(data) > 0:
                            metrics.first_audio_time = time.time() - metrics.start_time
                            if self.verbose:
                                self.log(f"[{call_index}] First audio at {metrics.first_audio_time:.2f}s")
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            if "closed" not in str(e).lower():
                metrics.errors.append(f"Receive error: {e}")

    async def run_test(self) -> TestResults:
        """Run the load test"""
        self.log("=" * 60)
        self.log(f"🚀 PIPECAT LOAD TEST STARTING")
        self.log("=" * 60)
        self.log(f"Target: {self.target_url}")
        self.log(f"Concurrent calls: {self.num_calls}")
        self.log(f"Call duration: {self.call_duration}s")
        self.log(f"Ramp-up time: {self.ramp_up_seconds}s")
        self.log("=" * 60)

        self.test_start_time = time.time()
        self.metrics = []

        # Calculate delay between call starts
        delay_between_calls = self.ramp_up_seconds / max(self.num_calls, 1)

        # Start calls with ramp-up
        tasks = []
        for i in range(self.num_calls):
            task = asyncio.create_task(self.simulate_call(i))
            tasks.append(task)

            # Progress indicator
            if (i + 1) % 5 == 0 or i == 0:
                self.log(f"Started {i + 1}/{self.num_calls} calls (active: {self.active_calls})")

            if i < self.num_calls - 1:
                await asyncio.sleep(delay_between_calls)

        self.log(f"All {self.num_calls} calls started. Waiting for completion...")

        # Wait for all calls to complete
        self.metrics = await asyncio.gather(*tasks)

        test_duration = time.time() - self.test_start_time

        # Calculate results
        return self._calculate_results(test_duration)

    def _calculate_results(self, test_duration: float) -> TestResults:
        """Calculate aggregated test results"""
        successful = [m for m in self.metrics if m.status == "completed"]
        failed = [m for m in self.metrics if m.status == "failed"]

        connect_times = [m.connect_time for m in successful if m.connect_time]
        first_audio_times = [m.first_audio_time for m in successful if m.first_audio_time]

        all_errors = []
        for m in self.metrics:
            all_errors.extend(m.errors)

        return TestResults(
            total_calls=len(self.metrics),
            successful_calls=len(successful),
            failed_calls=len(failed),
            avg_connect_time=statistics.mean(connect_times) if connect_times else 0,
            max_connect_time=max(connect_times) if connect_times else 0,
            min_connect_time=min(connect_times) if connect_times else 0,
            avg_first_audio_time=statistics.mean(first_audio_times) if first_audio_times else 0,
            total_bytes_sent=sum(m.bytes_sent for m in self.metrics),
            total_bytes_received=sum(m.bytes_received for m in self.metrics),
            errors=all_errors,
            duration_seconds=test_duration,
            calls_per_second=len(successful) / test_duration if test_duration > 0 else 0
        )

    def print_results(self, results: TestResults):
        """Print formatted test results"""
        print("\n")
        print("=" * 60)
        print("📊 LOAD TEST RESULTS")
        print("=" * 60)

        # Success rate
        success_rate = (results.successful_calls / results.total_calls * 100) if results.total_calls > 0 else 0
        status_emoji = "✅" if success_rate >= 95 else "⚠️" if success_rate >= 80 else "❌"

        print(f"\n{status_emoji} SUCCESS RATE: {success_rate:.1f}%")
        print(f"   Total calls: {results.total_calls}")
        print(f"   Successful: {results.successful_calls}")
        print(f"   Failed: {results.failed_calls}")
        print(f"   Peak concurrent: {self.peak_concurrent}")

        print(f"\n⏱️  TIMING")
        print(f"   Test duration: {results.duration_seconds:.1f}s")
        print(f"   Avg connect time: {results.avg_connect_time:.2f}s")
        print(f"   Min connect time: {results.min_connect_time:.2f}s")
        print(f"   Max connect time: {results.max_connect_time:.2f}s")
        print(f"   Avg first audio: {results.avg_first_audio_time:.2f}s")

        print(f"\n📦 DATA TRANSFER")
        print(f"   Total sent: {results.total_bytes_sent / 1024 / 1024:.2f} MB")
        print(f"   Total received: {results.total_bytes_received / 1024 / 1024:.2f} MB")

        if results.errors:
            print(f"\n❌ ERRORS ({len(results.errors)} total)")
            # Group and count errors
            error_counts = {}
            for e in results.errors:
                error_counts[e] = error_counts.get(e, 0) + 1
            for error, count in sorted(error_counts.items(), key=lambda x: -x[1])[:5]:
                print(f"   [{count}x] {error[:80]}")

        print("\n" + "=" * 60)

        # Recommendations
        print("\n💡 RECOMMENDATIONS")
        if success_rate >= 95:
            print("   ✅ System handles this load well!")
            if self.num_calls < 40:
                print(f"   → Try increasing to {self.num_calls + 10} calls")
        elif success_rate >= 80:
            print("   ⚠️ Some failures detected - approaching capacity limit")
            print("   → Check container CPU/memory usage")
            print("   → Review error logs for 429 rate limits")
        else:
            print("   ❌ Significant failures - system overloaded")
            print("   → Reduce concurrent calls")
            print("   → Check API rate limits (ElevenLabs/Deepgram)")
            print("   → Scale up containers or VM resources")

        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Pipecat Voice Agent Load Tester")
    parser.add_argument("--url", type=str, default=None,
                       help="WebSocket URL (default: ws://localhost:8000/ws)")
    parser.add_argument("--calls", type=int, default=10,
                       help="Number of concurrent calls (default: 10)")
    parser.add_argument("--duration", type=int, default=30,
                       help="Duration of each call in seconds (default: 30)")
    parser.add_argument("--ramp-up", type=int, default=10,
                       help="Ramp-up time in seconds (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")

    args = parser.parse_args()

    # Default URL
    target_url = args.url or os.getenv("PIPECAT_TEST_URL", "ws://localhost:8000/ws")

    tester = LoadTester(
        target_url=target_url,
        num_calls=args.calls,
        call_duration=args.duration,
        ramp_up_seconds=args.ramp_up,
        verbose=args.verbose
    )

    results = await tester.run_test()
    tester.print_results(results)

    # Return exit code based on success rate
    success_rate = results.successful_calls / results.total_calls * 100 if results.total_calls > 0 else 0
    sys.exit(0 if success_rate >= 80 else 1)


if __name__ == "__main__":
    asyncio.run(main())
