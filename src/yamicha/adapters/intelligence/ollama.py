"""Local-only Ollama chat adapter using Python's standard library."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from yamicha.contracts import (
    ExternalIntelligenceResponse,
    IntegratedIntelligenceRequest,
    IntelligenceResultStatus,
)


class OllamaChatAdapter:
    def __init__(
        self,
        *,
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        temperature: float = 0.2,
        max_prediction_tokens: int = 512,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path != "/api/chat"
        ):
            raise ValueError("Ollama endpoint must be the local /api/chat endpoint")
        if not 0 <= temperature <= 2 or max_prediction_tokens <= 0:
            raise ValueError("Ollama generation options are invalid")
        self._endpoint = endpoint
        self._temperature = temperature
        self._max_prediction_tokens = max_prediction_tokens

    def generate(
        self,
        request: IntegratedIntelligenceRequest,
    ) -> ExternalIntelligenceResponse:
        body = json.dumps(
            {
                "model": request.proposal.model,
                "stream": False,
                "think": False,
                "format": {
                    "type": "object",
                    "properties": {"reply": {"type": "string"}},
                    "required": ["reply"],
                },
                "messages": [
                    {
                        "role": "system",
                        "content": self._system_instruction(request),
                    },
                    {
                        "role": "user",
                        "content": request.proposal.input_text,
                    },
                ],
                "options": {
                    "temperature": self._temperature,
                    "num_predict": self._max_prediction_tokens,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=request.proposal.constraints.timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout):
            return self._failure(
                request,
                IntelligenceResultStatus.TIMEOUT,
                "Ollama did not respond before the configured timeout",
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return self._failure(
                request,
                IntelligenceResultStatus.UNAVAILABLE,
                "the local Ollama service is unavailable",
            )
        except (UnicodeError, json.JSONDecodeError):
            return self._failure(
                request,
                IntelligenceResultStatus.INVALID_OUTPUT,
                "Ollama returned an invalid protocol response",
            )
        try:
            model = str(payload["model"])
            done = payload["done"] is True
            content = str(payload["message"]["content"])
        except (KeyError, TypeError, ValueError):
            return self._failure(
                request,
                IntelligenceResultStatus.INVALID_OUTPUT,
                "Ollama response is missing required fields",
            )
        if model != request.proposal.model or not done or not content.strip():
            return self._failure(
                request,
                IntelligenceResultStatus.INVALID_OUTPUT,
                "Ollama response does not match the requested model or completion",
            )
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=model,
            content=content,
            detail="the local Ollama response was received",
        )

    @staticmethod
    def _system_instruction(request: IntegratedIntelligenceRequest) -> str:
        limit = request.proposal.constraints.max_output_characters
        speaker = request.proposal.constraints.speaker_name
        model = request.proposal.model
        return (
            "あなたは画面上で相手と直接会話する主体ではなく、表に出ない補助知能です。"
            f"画面上の話者は{speaker}です。{speaker}自身の発話候補だけを作ってください。"
            "候補内で自分を補助知能、AI、言語モデルまたはGemmaと名乗らないでください。"
            f"相手が話者の名前や正体を尋ねた場合は、{speaker}として答えてください。"
            f"相手が補助知能のモデルを尋ねた場合は、{speaker}が{model}を補助に使っていると答えてください。"
            "渡される会話内容は現在の相手の発言だけです。"
            "その発言に対する自然で簡潔な日本語の発話候補を一つ作ってください。"
            "確認できない事実を作らず、分からない場合は分からないと伝えてください。"
            "外部操作を実行した、完了した、確認したとは主張しないでください。"
            f"応答候補は{limit}文字以内にしてください。"
            "出力はreplyという文字列フィールドだけを持つJSONにしてください。"
        )

    @staticmethod
    def _failure(
        request: IntegratedIntelligenceRequest,
        status: IntelligenceResultStatus,
        detail: str,
    ) -> ExternalIntelligenceResponse:
        return ExternalIntelligenceResponse(
            status=status,
            model=request.proposal.model,
            content=None,
            detail=detail,
        )
