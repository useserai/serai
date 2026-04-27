import copy
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

_PROVIDER = (os.environ.get("MODEL_PROVIDER") or "openai").strip().lower()
_model: Optional[str] = None
_client: Any = None


def get_model_name() -> str:
    global _model
    if _model is not None:
        return _model
    if _PROVIDER == "anthropic":
        _model = (os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"
    elif _PROVIDER == "google":
        _model = (os.environ.get("GOOGLE_MODEL") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    else:
        _model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    return _model


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    if _PROVIDER == "anthropic":
        from anthropic import Anthropic
        _client = Anthropic()
    elif _PROVIDER == "google":
        from google import genai
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    else:
        from openai import OpenAI
        _client = OpenAI(timeout=120.0)
    return _client


def _adapt_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert JSON Schema to Gemini-compatible format.

    Gemini uses a strict subset of JSON Schema:
    - No array union types like ["number", "null"] — use nullable:true instead
    - No additionalProperties
    """
    schema = copy.deepcopy(schema)

    def process(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj
        # Convert ["X", "null"] union to {type: "X", nullable: true}
        if "type" in obj and isinstance(obj["type"], list):
            types = obj["type"]
            non_null = [t for t in types if t != "null"]
            obj["type"] = non_null[0] if non_null else "string"
            if "null" in types:
                obj["nullable"] = True
        obj.pop("additionalProperties", None)
        for key, value in obj.items():
            if isinstance(value, dict):
                obj[key] = process(value)
            elif isinstance(value, list):
                obj[key] = [process(item) if isinstance(item, dict) else item for item in value]
        return obj

    return process(schema)


def call_llm(prompt: str, schema: Dict[str, Any], schema_name: str, max_retries: int = 3) -> str:
    """Call the configured LLM provider with retry. Returns a JSON string matching schema."""
    import time

    client = _get_client()
    model = get_model_name()

    for attempt in range(max_retries):
        try:
            if _PROVIDER == "anthropic":
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    timeout=120.0,
                    tools=[{
                        "name": schema_name,
                        "description": "Return a structured evaluation result.",
                        "input_schema": schema,
                    }],
                    tool_choice={"type": "tool", "name": schema_name},
                    messages=[{"role": "user", "content": prompt}],
                )
                tool_block = next(b for b in response.content if b.type == "tool_use")
                return json.dumps(tool_block.input)

            elif _PROVIDER == "google":
                from google.genai import types
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_adapt_schema_for_gemini(schema),
                    ),
                )
                return response.text

            else:
                response = client.responses.create(
                    model=model,
                    input=prompt,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                )
                return response.output_text

        except Exception as e:
            is_retryable = any(s in str(e).lower() for s in ["timeout", "rate", "429", "503", "overloaded"])
            if not is_retryable or attempt == max_retries - 1:
                raise
            wait = 2 ** attempt * 2  # 2s, 4s, 8s
            print(f"  [llm retry] attempt {attempt + 1}/{max_retries}, waiting {wait}s: {e}")
            time.sleep(wait)