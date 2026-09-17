"""LLM inference module using Ollama.

Provides local LLM inference through the Ollama API.
"""

import logging
import httpx
import json
from typing import Optional, AsyncIterator

logger = logging.getLogger(__name__)


class OllamaLLM:
    """LLM inference via Ollama."""

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3", context_window: int = 4096) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.context_window = context_window
        self.client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                base_url=self.host,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
        return self.client

    async def chat_message(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = False,
    ) -> dict:
        """Send a chat request to Ollama and return the raw assistant message.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional tool/function definitions for tool-calling models
            stream: Whether to stream the response (ignored when tools is set)

        Returns:
            The assistant message dict, e.g. {"role", "content", "tool_calls"}
        """
        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream and not tools,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_ctx": self.context_window,
            }
        }
        if tools:
            payload["tools"] = tools

        try:
            if payload["stream"]:
                content = await self._chat_stream(client, payload)
                return {"role": "assistant", "content": content}
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {})
        except httpx.ConnectError:
            logger.error(f"Cannot connect to Ollama at {self.host}")
            raise ConnectionError(f"Ollama server not available at {self.host}")
        except Exception as e:
            logger.error(f"Ollama chat error: {e}")
            raise

    async def chat(self, messages: list[dict], stream: bool = False) -> str:
        """Send a chat request to Ollama and return the response text.

        Args:
            messages: List of message dicts with 'role' and 'content'
            stream: Whether to stream the response

        Returns:
            The LLM response text
        """
        message = await self.chat_message(messages, stream=stream)
        return message.get("content", "")

    async def _chat_stream(self, client: httpx.AsyncClient, payload: dict) -> str:
        """Stream chat response from Ollama."""
        payload["stream"] = True
        response = await client.post("/api/chat", json=payload)
        response.raise_for_status()

        full_text = ""
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("done"):
                    break
                chunk = data.get("message", {}).get("content", "")
                full_text += chunk
            except json.JSONDecodeError:
                continue

        return full_text

    async def generate(self, prompt: str) -> str:
        """Generate text from a prompt.

        Args:
            prompt: The input prompt

        Returns:
            Generated text
        """
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages)

    async def list_models(self) -> list[dict]:
        """List available Ollama models."""
        client = await self._get_client()
        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            return []

    async def check_health(self) -> bool:
        """Check if Ollama server is healthy."""
        client = await self._get_client()
        try:
            response = await client.get("/api/version")
            return response.status_code == 200
        except Exception:
            return False

    def is_available(self) -> bool:
        """Check if Ollama is available (synchronous check)."""
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            healthy = loop.run_until_complete(self.check_health())
            loop.close()
            return healthy
        except Exception:
            return False
