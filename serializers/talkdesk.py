#
# Vendored from pipecat-ai/pipecat PR #3467 (by poseneror)
# https://github.com/pipecat-ai/pipecat/pull/3467
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Talkdesk Media Streams WebSocket protocol serializer for Pipecat."""

import base64
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from loguru import logger
from pydantic import BaseModel

from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.utils import create_stream_resampler, pcm_to_ulaw, ulaw_to_pcm
from pipecat.frames.frames import (
    AudioRawFrame,
    ControlFrame,
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
    UninterruptibleFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class TalkdeskControlAction(Enum):
    """Control actions for Talkdesk call management."""

    HANGUP = "ok"
    ERROR = "error"
    ESCALATE = "escalate"


@dataclass
class TalkdeskControlFrame(ControlFrame, UninterruptibleFrame):
    """Control frame for Talkdesk-specific call management.

    Used to signal call termination or escalation to a human agent.
    This is an uninterruptible frame to ensure the stop message is always
    delivered to Talkdesk.
    """

    action: TalkdeskControlAction
    ring_group: Optional[str] = None


class TalkdeskFrameSerializer(FrameSerializer):
    """Serializer for Talkdesk Media Streams WebSocket protocol.

    Handles converting between Pipecat frames and Talkdesk's WebSocket
    media streams protocol. Supports audio conversion, DTMF events, and
    call control (hangup/escalation).
    """

    class InputParams(BaseModel):
        talkdesk_sample_rate: int = 8000
        sample_rate: Optional[int] = None

    def __init__(
        self,
        stream_sid: str,
        params: Optional[InputParams] = None,
    ):
        self._params = params or TalkdeskFrameSerializer.InputParams()
        self._stream_sid = stream_sid
        self._talkdesk_sample_rate = self._params.talkdesk_sample_rate
        self._sample_rate = 0  # Pipeline input rate

        self._input_resampler = create_stream_resampler()
        self._output_resampler = create_stream_resampler()
        self._stop_sent = False

    @property
    def type(self):
        return "text"

    async def setup(self, frame: StartFrame):
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, TalkdeskControlFrame):
            if self._stop_sent:
                return None
            self._stop_sent = True

            logger.info(f"Sending stop event to Talkdesk with command {frame.action.value}")

            return json.dumps(
                {
                    "event": "stop",
                    "streamSid": self._stream_sid,
                    "stop": {"command": frame.action.value, "ringGroup": frame.ring_group},
                }
            )
        elif isinstance(frame, InterruptionFrame):
            answer = {"event": "clear", "streamSid": self._stream_sid}
            return json.dumps(answer)
        elif isinstance(frame, AudioRawFrame):
            data = frame.audio

            serialized_data = await pcm_to_ulaw(
                data, frame.sample_rate, self._talkdesk_sample_rate, self._output_resampler
            )
            if serialized_data is None or len(serialized_data) == 0:
                return None

            payload = base64.b64encode(serialized_data).decode("utf-8")
            answer = {
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": payload},
            }

            return json.dumps(answer)
        elif isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        message = json.loads(data)
        event = message.get("event")

        if event == "media":
            payload_base64 = message.get("media", {}).get("payload")
            payload = base64.b64decode(payload_base64)

            deserialized_data = await ulaw_to_pcm(
                payload, self._talkdesk_sample_rate, self._sample_rate, self._input_resampler
            )
            if deserialized_data is None or len(deserialized_data) == 0:
                return None

            audio_frame = InputAudioRawFrame(
                audio=deserialized_data, num_channels=1, sample_rate=self._sample_rate
            )
            return audio_frame
        elif event == "dtmf":
            digit = message.get("dtmf", {}).get("digit")

            try:
                return InputDTMFFrame(KeypadEntry(digit))
            except ValueError:
                return None
        elif event == "stop":
            logger.debug("Received stop event from Talkdesk")
            self._stop_sent = True
            return None

        return None
