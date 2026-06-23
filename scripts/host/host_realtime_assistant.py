#!/usr/bin/env python3
"""Host-only Invoke voice assistant prototype.

This script is intentionally host-only: it uses the Mac microphone as input,
plays returned PCM to a selected CoreAudio output, and keeps OpenAI/Azure/Home
Assistant credentials on the trusted host. It never talks to the Invoke over
USB/network and never writes device storage.

Default mode is a dry run. Pass --connect to open audio devices and a Realtime
WebSocket.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

DEFAULT_SAMPLE_RATE = 24_000
DEFAULT_BLOCK_MS = 200
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"
HA_TOOL_NAME = "ha_conversation_process"

JsonObject = dict[str, Any]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: int

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0


def load_sounddevice() -> Any:
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required for audio device selection/streaming"
        ) from exc
    return sd


def query_audio_devices() -> list[AudioDevice]:
    sd = load_sounddevice()
    devices: list[AudioDevice] = []
    for idx, raw in enumerate(sd.query_devices()):
        devices.append(
            AudioDevice(
                index=idx,
                name=str(raw.get("name", "")),
                max_input_channels=int(raw.get("max_input_channels", 0)),
                max_output_channels=int(raw.get("max_output_channels", 0)),
                default_samplerate=int(float(raw.get("default_samplerate", 0))),
            )
        )
    return devices


def device_kind_matches(device: AudioDevice, kind: str) -> bool:
    if kind == "input":
        return device.is_input
    if kind == "output":
        return device.is_output
    raise ValueError(f"unknown device kind: {kind}")


def resolve_device(
    devices: Iterable[AudioDevice], selector: str | None, kind: str
) -> AudioDevice | None:
    candidates = [d for d in devices if device_kind_matches(d, kind)]
    if not candidates:
        return None
    if not selector:
        return None
    if selector.isdigit():
        wanted_index = int(selector)
        return next((d for d in candidates if d.index == wanted_index), None)
    selector_lower = selector.lower()
    return next((d for d in candidates if selector_lower in d.name.lower()), None)


def format_device(device: AudioDevice | None, kind: str) -> str:
    if device is None:
        return f"{kind}: default CoreAudio device"
    channel_count = (
        device.max_input_channels if kind == "input" else device.max_output_channels
    )
    return (
        f"{kind}: #{device.index} {device.name!r} "
        f"channels={channel_count} default_rate={device.default_samplerate}"
    )


def print_audio_device_plan(args: argparse.Namespace) -> None:
    if args.skip_device_query:
        print("audio: device query skipped")
        return

    devices = query_audio_devices()
    input_device = resolve_device(devices, args.input_device, "input")
    output_device = resolve_device(devices, args.output_device, "output")
    print(format_device(input_device, "input"))
    print(format_device(output_device, "output"))

    missing: list[str] = []
    if args.input_device and input_device is None:
        missing.append(f"input selector {args.input_device!r}")
    if args.output_device and output_device is None:
        missing.append(f"output selector {args.output_device!r}")
    if missing:
        message = "missing audio device(s): " + ", ".join(missing)
        if args.strict_devices:
            raise RuntimeError(message)
        print("warning:", message)


def openai_realtime_url(model: str) -> str:
    return f"{OPENAI_REALTIME_URL}?" + urllib.parse.urlencode({"model": model})


def normalize_azure_host(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("empty Azure endpoint")
    parsed = urllib.parse.urlparse(
        endpoint if "://" in endpoint else f"https://{endpoint}"
    )
    if not parsed.netloc:
        raise ValueError(f"invalid Azure endpoint: {endpoint!r}")
    return parsed.netloc.rstrip("/")


def azure_realtime_url(
    endpoint: str, deployment: str, preview_api_version: str | None
) -> str:
    host = normalize_azure_host(endpoint)
    if preview_api_version:
        query = urllib.parse.urlencode(
            {"api-version": preview_api_version, "deployment": deployment}
        )
        return f"wss://{host}/openai/realtime?{query}"
    query = urllib.parse.urlencode({"model": deployment})
    return f"wss://{host}/openai/v1/realtime?{query}"


def build_realtime_url(args: argparse.Namespace) -> str:
    if args.realtime_url:
        return args.realtime_url
    if args.provider == "openai":
        return openai_realtime_url(args.model)

    endpoint = args.azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = args.azure_deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    if not endpoint or not deployment:
        raise RuntimeError(
            "Azure provider requires --azure-endpoint/--azure-deployment "
            "or AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_DEPLOYMENT"
        )
    return azure_realtime_url(endpoint, deployment, args.azure_preview_api_version)


def api_key_env_name(args: argparse.Namespace) -> str:
    if args.api_key_env:
        return args.api_key_env
    if args.provider == "azure":
        return "AZURE_OPENAI_API_KEY"
    return "OPENAI_API_KEY"


def realtime_headers(args: argparse.Namespace) -> dict[str, str]:
    env_name = api_key_env_name(args)
    key = os.environ.get(env_name, "")
    if not key:
        raise RuntimeError(f"missing required API key environment variable: {env_name}")
    if args.provider == "azure":
        return {"api-key": key}
    headers = {"Authorization": f"Bearer {key}"}
    if args.safety_identifier:
        headers["OpenAI-Safety-Identifier"] = args.safety_identifier
    return headers


def redacted_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_query = [
        (key, "<redacted>" if key.lower() in {"api-key", "key"} else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(redacted_query))
    )


def ha_tool_schema() -> JsonObject:
    return {
        "type": "function",
        "name": HA_TOOL_NAME,
        "description": (
            "Send a final, user-intended smart-home request to Home Assistant "
            "Assist via the host-side /api/conversation/process boundary. "
            "Use only for Home Assistant actions/questions, never for general chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The concise command/question to pass to Home Assistant.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional BCP-47 language code, for example en.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional Home Assistant conversation agent id.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Optional Home Assistant conversation id for follow-up turns.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    }


def session_update_event(args: argparse.Namespace) -> JsonObject:
    instructions = args.instructions or (
        "You are a concise host-side voice assistant for a Harman Kardon Invoke "
        "prototype. Keep secrets on the host. Treat the Invoke as audio output "
        "only. For smart-home requests, call ha_conversation_process with the "
        "final text command instead of inventing device state."
    )
    session: JsonObject = {
        "type": "realtime",
        "instructions": instructions,
        "modalities": [args.modality],
        "audio": {
            "input": {
                "format": "pcm16",
                "turn_detection": {"type": "server_vad"},
            },
            "output": {
                "format": "pcm16",
                "voice": args.voice,
            },
        },
        "tools": [ha_tool_schema()],
        "tool_choice": "auto",
        "tracing": "auto" if args.tracing else None,
    }
    if args.reasoning_effort:
        session["reasoning"] = {"effort": args.reasoning_effort}
    return {"type": "session.update", "session": session}


@dataclass
class HomeAssistantConversationClient:
    base_url: str
    token: str
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> HomeAssistantConversationClient | None:
        base_url = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
        token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
        if not base_url or not token:
            return None
        return cls(base_url=base_url, token=token)

    def process_sync(
        self,
        *,
        text: str,
        language: str | None = None,
        agent_id: str | None = None,
        conversation_id: str | None = None,
    ) -> JsonObject:
        payload: JsonObject = {"text": text}
        if language:
            payload["language"] = language
        if agent_id:
            payload["agent_id"] = agent_id
        if conversation_id:
            payload["conversation_id"] = conversation_id

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/conversation/process",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                response_body = response.read().decode("utf-8")
                return json.loads(response_body) if response_body else {}
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return {
                "error": "home_assistant_http_error",
                "status": exc.code,
                "body": error_body,
            }
        except urllib.error.URLError as exc:
            return {"error": "home_assistant_url_error", "reason": str(exc.reason)}

    async def process(self, arguments: JsonObject) -> JsonObject:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return {"error": "missing_text"}
        return await asyncio.to_thread(
            self.process_sync,
            text=text,
            language=optional_str(arguments.get("language")),
            agent_id=optional_str(arguments.get("agent_id")),
            conversation_id=optional_str(arguments.get("conversation_id")),
        )


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class AudioStreams:
    def __init__(self, args: argparse.Namespace, loop: asyncio.AbstractEventLoop):
        self.args = args
        self.loop = loop
        self.sd = load_sounddevice()
        self.input_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=args.audio_queue_size
        )
        self.input_stream: Any | None = None
        self.output_stream: Any | None = None

    def _enqueue_input(self, chunk: bytes) -> None:
        try:
            self.input_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            eprint("input audio queue full; dropping chunk")

    def _input_callback(
        self,
        indata: bytes,
        _frames: int,
        _time: Any,
        status: Any,
    ) -> None:
        if status:
            self.loop.call_soon_threadsafe(eprint, f"input audio status: {status}")
        self.loop.call_soon_threadsafe(self._enqueue_input, bytes(indata))

    def _stream_device_index(self, selector: str | None, kind: str) -> int | None:
        if not selector:
            return None
        if selector.isdigit():
            return int(selector)
        device = resolve_device(query_audio_devices(), selector, kind)
        if device is None:
            raise RuntimeError(f"no {kind} audio device matches {selector!r}")
        return device.index

    def __enter__(self) -> AudioStreams:
        input_device = self._stream_device_index(self.args.input_device, "input")
        output_device = self._stream_device_index(self.args.output_device, "output")
        self.input_stream = self.sd.RawInputStream(
            samplerate=self.args.sample_rate,
            blocksize=max(1, int(self.args.sample_rate * self.args.block_ms / 1000)),
            channels=1,
            dtype="int16",
            device=input_device,
            callback=self._input_callback,
        )
        self.output_stream = self.sd.RawOutputStream(
            samplerate=self.args.sample_rate,
            channels=1,
            dtype="int16",
            device=output_device,
        )
        self.input_stream.start()
        self.output_stream.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        for stream in (self.input_stream, self.output_stream):
            if stream is not None:
                stream.stop()
                stream.close()

    def write_output(self, pcm_bytes: bytes) -> None:
        if self.output_stream is not None:
            self.output_stream.write(pcm_bytes)


async def send_mic_audio(ws: Any, audio: AudioStreams) -> None:
    while True:
        pcm = await audio.input_queue.get()
        await ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )


async def handle_function_call(
    ws: Any, item: JsonObject, ha_client: HomeAssistantConversationClient | None
) -> None:
    call_id = str(item.get("call_id", ""))
    name = str(item.get("name", ""))
    if name != HA_TOOL_NAME:
        output = {"error": "unknown_tool", "name": name}
    elif ha_client is None:
        output = {
            "error": "home_assistant_not_configured",
            "required_env": ["HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"],
        }
    else:
        try:
            arguments = json.loads(str(item.get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            arguments = {"_json_error": str(exc)}
        if "_json_error" in arguments:
            output = {"error": "invalid_tool_arguments", **arguments}
        else:
            output = await ha_client.process(arguments)

    await ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output),
                },
            }
        )
    )
    await ws.send(json.dumps({"type": "response.create"}))


async def receive_events(ws: Any, audio: AudioStreams) -> None:
    ha_client = HomeAssistantConversationClient.from_env()
    async for raw_message in ws:
        event = json.loads(raw_message)
        event_type = event.get("type")
        if event_type == "response.output_audio.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                audio.write_output(base64.b64decode(delta))
        elif event_type in {
            "response.output_text.delta",
            "response.output_audio_transcript.delta",
        }:
            delta = event.get("delta")
            if delta:
                print(delta, end="", flush=True)
        elif event_type == "response.done":
            response = event.get("response", {})
            for item in (
                response.get("output", []) if isinstance(response, dict) else []
            ):
                if isinstance(item, dict) and item.get("type") == "function_call":
                    await handle_function_call(ws, item, ha_client)
        elif event_type == "error":
            eprint("Realtime error:", json.dumps(event, indent=2))


async def run_connected(args: argparse.Namespace) -> None:
    try:
        import websockets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("websockets is required for --connect") from exc

    url = build_realtime_url(args)
    headers = realtime_headers(args)
    loop = asyncio.get_running_loop()
    async with websockets.connect(url, extra_headers=headers) as ws:
        await ws.send(json.dumps(session_update_event(args)))
        with AudioStreams(args, loop) as audio:
            await asyncio.gather(send_mic_audio(ws, audio), receive_events(ws, audio))


def dry_run(args: argparse.Namespace) -> int:
    url = build_realtime_url(args)
    print("mode: dry-run (no network, no audio streams)")
    print(f"provider: {args.provider}")
    print(f"realtime_url: {redacted_url(url)}")
    print(
        f"api_key_env: {api_key_env_name(args)} ({'set' if os.environ.get(api_key_env_name(args)) else 'missing'})"
    )
    print(f"sample_rate: {args.sample_rate} pcm16 mono")
    print_audio_device_plan(args)
    print(
        "home_assistant:",
        "configured"
        if HomeAssistantConversationClient.from_env()
        else "not configured",
    )
    print("session_update:")
    print(json.dumps(session_update_event(args), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connect",
        action="store_true",
        help="open Realtime WebSocket and audio streams",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print resolved plan without side effects",
    )
    parser.add_argument("--provider", choices=("openai", "azure"), default="openai")
    parser.add_argument(
        "--model", default="gpt-realtime-2", help="OpenAI Realtime model"
    )
    parser.add_argument("--realtime-url", help="override full Realtime WebSocket URL")
    parser.add_argument(
        "--api-key-env", help="environment variable containing provider API key"
    )
    parser.add_argument("--safety-identifier", default="hk-invoke-host-prototype")
    parser.add_argument(
        "--azure-endpoint", help="Azure OpenAI endpoint host or https URL"
    )
    parser.add_argument(
        "--azure-deployment", help="Azure OpenAI realtime deployment name"
    )
    parser.add_argument(
        "--azure-preview-api-version",
        help="use Azure preview URL shape with this api-version",
    )
    parser.add_argument(
        "--input-device", help="CoreAudio input device index or name substring"
    )
    parser.add_argument(
        "--output-device",
        help="CoreAudio output device index or name substring, e.g. HK Invoke",
    )
    parser.add_argument(
        "--strict-devices",
        action="store_true",
        help="fail if selected devices are missing",
    )
    parser.add_argument(
        "--skip-device-query",
        action="store_true",
        help="avoid CoreAudio device enumeration",
    )
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--block-ms", type=int, default=DEFAULT_BLOCK_MS)
    parser.add_argument("--audio-queue-size", type=int, default=20)
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--modality", choices=("audio", "text"), default="audio")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--instructions")
    parser.add_argument("--tracing", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.connect:
        args.dry_run = True
    if args.dry_run and args.connect:
        raise SystemExit("choose either --dry-run or --connect")
    try:
        if args.dry_run:
            return dry_run(args)
        asyncio.run(run_connected(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI needs a concise boundary error.
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
