"""
Lightweight LLM connection helper.

Purpose
- Provide a single, simple entry-point to call an OpenAI-compatible model (default: "gpt-5-mini").
- Other modules should only supply a prompt; this class handles auth, endpoint, and parsing.

Usage
	from src.utils.llm_connection import LLMConnection

	llm = LLMConnection()  # reads OPENAI_API_KEY from environment
	text = llm.generate_text("Summarize this in one sentence: ...")

Environment
- OPENAI_API_KEY: required unless api_key is passed explicitly
- OPENAI_BASE_URL: optional (defaults to https://api.openai.com)
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional, Type, TypeVar
from loguru import logger
from openai import OpenAI
from pydantic import BaseModel

class LLMConnection:
	def __init__(
		self,
		*,
		api_key: Optional[str] = None,
		model: str = "gpt-5-mini",
		timeout: float = 30.0,
		system_prompt: Optional[str] = None
	) -> None:
		self.api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
		self.model = model
		self.client = OpenAI(
			api_key=self.api_key
		)
		self.timeout = timeout
		self.system_prompt = system_prompt

		if not self.api_key:
			logger.warning("OPENAI_API_KEY not set; LLM calls will fail until provided")

	def generate_structured(self, prompt: str, json_structure, *, system: Optional[str] = None) -> Optional[str]:
		"""Generate structured output using a Pydantic model."""
		if not self.api_key:
			logger.error("No API key provided - cannot make LLM call")
			return None
			
		try:
			input_content = []
			
			# Add system prompt if provided
			if system or self.system_prompt:
				system_text = system or self.system_prompt
				input_content.append({"role": "system", "content": [{"type": "input_text", "text": system_text}]})
			
			# Add user prompt
			input_content.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})

			response = self.client.responses.parse(
				model=self.model,
				input=input_content,
				text_format=json_structure,
				reasoning={"effort": "medium"},
				tools=[],
				store=True
			)

			if response.output_parsed:
				return response.output_parsed
			else:
				logger.warning("No valid JSON found in LLM response")
				return None
				
		except Exception as e:
			logger.error(f"Structured LLM call failed: {e}")
			return None

	def generate_text(self, prompt: str, *, system: Optional[str] = None) -> str:
		"""Send a prompt and return a single text string."""
		if not self.api_key:
			logger.error("No API key provided - cannot make LLM call")
			return ""
		try:
			input_content = []
			
			# Add system prompt if provided
			if system or self.system_prompt:
				system_text = system or self.system_prompt
				input_content.append({"role": "system", "content": [{"type": "input_text", "text": system_text}]})
			
			# Add user prompt
			input_content.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})

			response = self.client.responses.parse(
				model=self.model,
				input=input_content,
				reasoning={"effort": "medium"},
				tools=[],
				store=True
			)
			
			# Extract the text content from the response
			if hasattr(response, 'text') and response.text:
				return str(response.text).strip()
			else:
				logger.warning("Unexpected response format from LLM")
				return ""
				
		except Exception as e:
			logger.error(f"LLM call failed: {e}")
			return ""

	def ping(self) -> bool:
		"""Quick connectivity check (expects a 200 and non-empty content)."""
		try:
			text = self.generate_text("Reply with OK.")
			return bool(text)
		except Exception as e:  # safety net
			logger.error(f"LLM ping failed: {e}")
			return False
