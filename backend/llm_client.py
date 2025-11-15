import json
import os
from typing import Any, Dict

import requests


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        use_fallback_on_error: bool = True,
    ) -> None:
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
        self.use_fallback_on_error = use_fallback_on_error

    def _generate(self, prompt: str, max_tokens: int = 512) -> str:
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
        except Exception as exc:  # noqa: BLE001
            if self.use_fallback_on_error:
                print(f"[LLMClient] Error calling Ollama, falling back: {exc}")
                return ""
            raise

    def answer_with_context(self, question: str, context_snippets: list[dict[str, str]]) -> str:
        context_texts = []
        for s in context_snippets:
            label = s.get("label", "Context")
            text = s.get("text", "")
            context_texts.append(f"{label}: {text}")
        context_block = "\n".join(context_texts)

        prompt = (
            "You are a helpful university teaching assistant helping a student during office hours. "
            "Below is some context from course documents and past questions, followed by the student's question. "
            "Use the context when it is relevant. If you are not sure, say you are not completely sure and suggest what to ask the TA.\n\n"
            "CONTEXT:\n"
            f"{context_block}\n\n"
            "STUDENT QUESTION:\n"
            f"{question}\n\n"
            "Give a concise, student-friendly answer (2-5 sentences)."
        )

        reply = self._generate(prompt, max_tokens=400).strip()
        if not reply:
            return (
                "I couldn't generate an automatic answer right now. "
                "Please ask the TA, and consider checking your lecture notes and assignment description."
            )
        return reply
