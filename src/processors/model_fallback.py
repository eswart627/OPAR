# src/processors/model_fallback.py
# Purpose: Dynamic LLM fallback engine for Gemini models defined in models.ini

import os
import time
import configparser
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path.cwd() / ".env", override=True)

class GeminiFallbackClient:
    """
    Client that reads models.ini and executes generations with automatic
    fallback if any model hits quota limits, high traffic, or errors.
    """
    def __init__(self, ini_path: Optional[str] = None):
        if ini_path:
            self.ini_path = Path(ini_path)
        elif (Path.cwd() / "config" / "models.ini").exists():
            self.ini_path = Path.cwd() / "config" / "models.ini"
        else:
            self.ini_path = Path.cwd() / "models.ini"
        self.api_key = os.environ.get("GOOGLE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_GEMINI_API_KEY not found in environment or .env file.")

        self.models: List[str] = []
        self.temperature: float = 0.0
        self.max_retries: int = 2
        self.load_config()

        # Initialize the official google.genai Client
        from google import genai
        self.client = genai.Client(api_key=self.api_key)

    def load_config(self) -> None:
        if not self.ini_path.exists():
            # Default fallback list if models.ini is missing
            self.models = [
                "gemini-3.1-flash-lite",
                "gemini-2.5-flash-lite",
                "gemini-3-flash-preview",
                "gemini-2.5-flash",
                "gemini-3.8-flash",
            ]
            self.temperature = 0.0
            return

        config = configparser.ConfigParser()
        config.read(self.ini_path)

        if "gemini_models" in config:
            self.models = [
                val.strip() for key, val in config["gemini_models"].items() if val.strip()
            ]

        if "settings" in config:
            self.temperature = config["settings"].getfloat("temperature", 0.0)
            self.max_retries = config["settings"].getint("max_retries_per_model", 2)

    def generate_text(self, prompt: str) -> str:
        """
        Generates text by attempting each model in models.ini in order.
        Switches to the next model upon any quota or availability error.
        """
        last_error = None

        for idx, model_name in enumerate(self.models):
            for attempt in range(self.max_retries):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    if response and response.text:
                        return response.text.strip()
                    return ""
                except Exception as e:
                    last_error = e
                    err_msg = str(e)
                    # Check if error is quota exhaustion or temporary unavailability
                    is_exhausted_or_unavailable = any(
                        code in err_msg for code in ["429", "503", "404", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "NOT_FOUND"]
                    )
                    if is_exhausted_or_unavailable:
                        print(f"[FALLBACK][WARN] Model '{model_name}' hit error ({err_msg[:90]}...).")
                        break  # Break retry loop to switch to next model immediately
                    else:
                        print(f"[FALLBACK][WARN] Model '{model_name}' attempt {attempt + 1} error: {err_msg[:90]}")
                        time.sleep(1)

            # Move to next model
            next_model = self.models[idx + 1] if idx + 1 < len(self.models) else "None"
            if next_model != "None":
                print(f"[FALLBACK][INFO] Switching from '{model_name}' to next model: '{next_model}'")

        raise RuntimeError(f"All models in models.ini failed. Last error: {last_error}")


# ---------------------------------------------------------------------
# LangChain Integration
# ---------------------------------------------------------------------
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration

class GeminiFallbackChatModel(BaseChatModel):
    """
    LangChain compatible chat model with automatic fallback across models.ini.
    Can be plugged directly into LangChain chains: prompt | llm | parser.
    """
    def __init__(self, ini_path: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._fallback_client = GeminiFallbackClient(ini_path=ini_path)

    @property
    def _llm_type(self) -> str:
        return "gemini-fallback"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Convert messages into a single prompt text
        prompt_parts = []
        for msg in messages:
            prompt_parts.append(f"{msg.type.upper()}: {msg.content}")
        full_prompt = "\n".join(prompt_parts)

        output_text = self._fallback_client.generate_text(full_prompt)
        generation = ChatGeneration(message=AIMessage(content=output_text))
        return ChatResult(generations=[generation])
