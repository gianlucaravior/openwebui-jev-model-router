"""
title: JEV Semantic Router Filter
author: gianlucaravior
author_url: https://github.com/gianlucaravior
funding_url: https://github.com/gianlucaravior/openwebui-jev-model-router
version: 3.0.0
required_open_webui_version: 0.9.1
description: Routes Open WebUI requests to configured chat models using Cloudflare JEV or Clef.
Model selection considers model descriptions, capabilities, context profiles, tools and Voice Mode.

Enable debug mode (valves.debug = True) to see detailed routing diagnostics.
"""

import logging
import json
import re
import base64
from typing import (
    Callable,
    Awaitable,
    Any,
    Optional,
    List,
    Dict,
    TypedDict,
)
from pydantic import BaseModel, Field
from fastapi import Request
import httpx
from open_webui.utils.misc import get_last_user_message
from open_webui.utils.payload import convert_payload_openai_to_ollama
from open_webui.models.users import UserModel, Users
from open_webui.models.files import FileMetadataResponse, Files
from open_webui.models.models import ModelUserResponse, ModelModel, Models

name = "semantic_router"

# Setup logger
logger = logging.getLogger(name)
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.set_name(name)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False


class ModelInfo(TypedDict):
    """Model information structure for routing decisions"""

    id: str
    name: str
    description: str
    vision_capable: bool
    capabilities: Dict[str, Any]
    tools: List[str]
    function_calling: str
    created_at: Optional[int]
    updated_at: Optional[int]


class RouterResponse(TypedDict):
    """Normalized response structure from Cloudflare's decision model."""

    selected_model_id: str
    reasoning: str
    context_need: float
    current_capacity: Optional[float]


def is_vision_capable(model_data: Dict[str, Any]) -> bool:
    """Check if a model has vision capabilities"""
    meta = model_data.get("meta") or {}
    if not isinstance(meta, dict):
        return False
    capabilities = meta.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        return False
    return bool(capabilities.get("vision", False))


def clean_ollama_params(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Remove Ollama-specific parameters that cause errors with OpenAI endpoints.
    Called when routing TO an OpenAI endpoint.
    """
    clean = payload.copy()

    if "options" in clean:
        options = clean.pop("options")

        if "num_predict" in options:
            clean["max_tokens"] = options["num_predict"]

        for param in [
            "temperature",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "seed",
            "stop",
        ]:
            if param in options and param not in clean:
                clean[param] = options[param]

    if "format" in clean:
        format_val = clean.pop("format")
        if isinstance(format_val, dict):
            clean["response_format"] = {
                "type": "json_schema",
                "json_schema": {"schema": format_val},
            }
        elif format_val == "json":
            clean["response_format"] = {"type": "json_object"}

    if "system" in clean:
        system_content = clean.pop("system")
        sys_msg: dict[str, Any] = {"role": "system", "content": system_content}
        clean["messages"] = [sys_msg] + clean.get("messages", [])

    return clean


def context_profile_from_score(score: float) -> str:
    """Map the evaluator's ordered context score to the documented S/M/L bands."""
    if score < 0.5:
        return "S"
    if score < 1.5:
        return "M"
    return "L"


class Filter:
    class Valves(BaseModel):
        router_entry_model_id: str = Field(
            default="",
            description="Open WebUI model ID users select to start automatic routing",
        )
        filter_id: str = Field(
            default="semantic_router",
            description="Open WebUI filter/function ID used to keep the router active after model switching",
        )
        fallback_model_id: str = Field(
            default="",
            description="Open WebUI chat model ID used when routing fails; empty keeps the incoming model",
        )
        text_decision_model: str = Field(
            default="typesafe/jev",
            description="Cloudflare decision model for text-only routing: typesafe/jev, @cf/cloudflare/clef, or @cf/cloudflare/clef-flash",
        )
        image_text_decision_model: str = Field(
            default="@cf/cloudflare/clef-flash",
            description="Cloudflare decision model for image-and-text routing; use a Clef model for image understanding",
        )
        cloudflare_account_id: str = Field(
            default="", description="Cloudflare account ID used by the AI REST API"
        )
        cloudflare_api_token: str = Field(
            default="",
            description="Secret Cloudflare API token with Workers AI Read permission",
            json_schema_extra={"input": {"type": "password"}},
        )
        cloudflare_gateway_id: str = Field(
            default="", description="Optional Cloudflare AI Gateway ID"
        )
        router_instructions: str = Field(
            default=(
                "Select the single best available Open WebUI chat model for the user's task. "
                "Use only the supplied model descriptions, capabilities, context profiles, and available tools. "
                "Do not infer capabilities that are not stated. Prefer the least costly model that can reliably "
                "complete the task. For voice, prefer fast direct responses and avoid extended reasoning. "
                "Choose the context requirement conservatively. The model descriptions may "
                "use S (up to 100K tokens), M (up to 250K tokens), or L (up to 1M tokens) to describe context size."
            ),
            description="Additional instructions for structured routing decisions (not a chat system prompt)",
        )
        banned_models: List[str] = Field(
            default_factory=list,
            description="Model IDs excluded from routing. Other candidates' descriptions are used by JEV/Clef to judge capabilities, tools and S/M/L context capacity.",
        )
        allowed_models: List[str] = Field(
            default_factory=list,
            description="Optional model-ID whitelist. The descriptions of included models are assessed for capabilities, tools and S/M/L context capacity.",
        )
        show_reasoning: bool = Field(
            default=False, description="Display routing reasoning in chat"
        )
        status: bool = Field(default=True, description="Show status updates in chat")
        debug: bool = Field(default=False, description="Enable debug logging")
        request_timeout_seconds: float = Field(
            default=20.0, ge=1.0, le=120.0,
            description="Timeout for Cloudflare decision requests",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.__request__: Optional[Request] = None
        self.__user__: Optional[UserModel] = None
        self.__model__: Optional[Dict[str, Any]] = None
        # Store routing decision for this request (persists between inlet/outlet)
        self._routed_model_id: Optional[str] = None
        self._routed_model_knowledge: Optional[List[Dict[str, Any]]] = None
        self._routed_model_tools: Optional[List[str]] = None

    @staticmethod
    def _is_voice_request(body: Dict[str, Any], metadata: Optional[Dict[str, Any]]) -> bool:
        """Open WebUI Voice Mode sets body.features.voice before inlet filters run."""
        features = body.get("features") or {}
        metadata = metadata or body.get("metadata") or {}
        return bool(
            features.get("voice")
            or metadata.get("voice")
            or metadata.get("audio")
        )

    @staticmethod
    def _encode_route_state(state: Dict[str, Any]) -> str:
        raw = json.dumps(state, separators=(",", ":")).encode("utf-8")
        bits = "".join(f"{byte:08b}" for byte in raw)
        invisible_bits = bits.translate(str.maketrans("01", "\u200b\u200c"))
        return "\u2063\u2063" + invisible_bits + "\u2063\u2063"

    @staticmethod
    def _decode_route_state(value: str) -> Dict[str, Any]:
        if value and all(character in "\u200b\u200c" for character in value):
            bits = value.translate(str.maketrans("\u200b\u200c", "01"))
            if not bits or len(bits) % 8:
                raise ValueError("Invalid invisible route marker")
            raw = bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8))
            decoded = json.loads(raw.decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {}

        if value.startswith("v1:"):
            encoded = value[3:]
            encoded += "=" * (-len(encoded) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {}

        return {"model_id": value}

    def _has_images(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if the message history contains images"""
        if not messages:
            return False

        last_user_message = next(
            (message for message in reversed(messages) if message.get("role") == "user"),
            messages[-1],
        )
        for message in [last_user_message]:
            content = message.get("content", "")
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") == "image_url"
                for item in content
            ):
                return True
            if message.get("images"):
                return True
        return False

    async def _get_full_model_data(self, model_id: str) -> Dict[str, Any]:
        """Construct a complete model data dictionary from app.state.MODELS and DB"""
        models_state = getattr(self.__request__.app.state, "MODELS", {})
        model_info = models_state.get(model_id, {})
        model_db_info = await Models.get_model_by_id(model_id)

        meta = {}
        params = {}

        info_meta = model_info.get("info", {}).get("meta", {})
        if isinstance(info_meta, dict):
            meta.update(info_meta)

        info_params = model_info.get("info", {}).get("params", {})
        if isinstance(info_params, dict):
            params.update(info_params)

        is_active = True
        created_at = None
        updated_at = None
        base_model_id = model_info.get("info", {}).get("base_model_id")
        owned_by = model_info.get("owned_by") or model_info.get("info", {}).get(
            "owned_by", "custom"
        )

        if model_db_info:
            is_active = getattr(model_db_info, "is_active", is_active)
            created_at = getattr(model_db_info, "created_at", created_at)
            updated_at = getattr(model_db_info, "updated_at", updated_at)
            base_model_id = getattr(model_db_info, "base_model_id", base_model_id)

            db_meta = model_db_info.meta
            if hasattr(db_meta, "model_dump"):
                db_meta = db_meta.model_dump()
            if isinstance(db_meta, dict):
                meta.update(db_meta)

            db_params = model_db_info.params
            if hasattr(db_params, "model_dump"):
                db_params = db_params.model_dump()
            if isinstance(db_params, dict):
                params.update(db_params)

        pipeline = meta.get("pipeline", {}) or model_info.get("pipeline", {})

        return {
            "id": model_id,
            "name": model_info.get("name", model_id),
            "meta": meta,
            "params": params,
            "is_active": is_active,
            "created_at": created_at,
            "updated_at": updated_at,
            "pipeline": pipeline,
            "base_model_id": base_model_id,
            "owned_by": owned_by,
        }

    async def _get_available_models(
        self,
        filter_vision: bool = False,
    ) -> List[ModelInfo]:
        """
        Get available models for routing with proper type handling

        Args:
            filter_vision: If True, only return vision-capable models
        """
        available: List[ModelInfo] = []
        models_state = getattr(self.__request__.app.state, "MODELS", {})

        if self.valves.debug:
            logger.debug(
                f"Processing {len(models_state)} models for routing (filter_vision={filter_vision})"
            )

        for model_id in models_state.keys():
            if model_id == self.valves.router_entry_model_id:
                continue
            model_dict = await self._get_full_model_data(model_id)
            model_id = model_dict.get("id")
            meta = model_dict.get("meta") or {}
            if not isinstance(meta, dict):
                meta = {}
            pipeline = model_dict.get("pipeline") or {}
            pipeline_type = pipeline.get("type") if isinstance(pipeline, dict) else None

            if not model_id or pipeline_type == "filter":
                continue

            is_active = model_dict.get("is_active", False)

            if not is_active:
                continue

            if (
                self.valves.allowed_models
                and model_id not in self.valves.allowed_models
            ):
                continue

            if model_id in self.valves.banned_models:
                continue

            description = meta.get("description", "")

            is_vision = is_vision_capable(model_dict)
            if filter_vision and not is_vision:
                continue

            model_info: ModelInfo = {
                "id": model_id,
                "name": model_dict.get("name", model_id),
                "description": description,
                "vision_capable": is_vision,
                "capabilities": meta.get("capabilities", {}),
                "tools": self._model_tool_descriptions(model_dict),
                "function_calling": self._model_function_calling(model_dict),
                "created_at": model_dict.get("created_at"),
                "updated_at": model_dict.get("updated_at"),
            }
            available.append(model_info)

        logger.info(f"Found {len(available)} available models for routing")
        if available and self.valves.debug:
            model_list = ", ".join([f"{m['name']} ({m['id']})" for m in available])
            logger.debug(f"Available models: {model_list}")

        return available

    def _model_tool_descriptions(self, model_data: Dict[str, Any]) -> List[str]:
        meta = model_data.get("meta") or {}
        params = model_data.get("params") or {}
        if not isinstance(meta, dict):
            meta = {}
        if not isinstance(params, dict):
            params = {}
        tool_ids = (
            meta.get("toolIds", [])
            or params.get("toolIds", [])
            or params.get("tools", [])
            or []
        )
        if not isinstance(tool_ids, list):
            tool_ids = [tool_ids]
        builtin_tools = meta.get("builtinTools", []) or []
        if isinstance(builtin_tools, dict):
            builtin_tools = [key for key, enabled in builtin_tools.items() if enabled]
        if not isinstance(builtin_tools, list):
            builtin_tools = [builtin_tools]
        feature_tools = meta.get("defaultFeatureIds", []) or []
        if not isinstance(feature_tools, list):
            feature_tools = [feature_tools]
        model_features = meta.get("features", {}) or {}
        if isinstance(model_features, dict):
            feature_tools.extend(key for key, enabled in model_features.items() if enabled)
        return list(dict.fromkeys(
            str(item.get("id", item) if isinstance(item, dict) else item)
            for item in list(tool_ids) + list(builtin_tools) + list(feature_tools)
        ))

    @staticmethod
    def _model_function_calling(model_data: Dict[str, Any]) -> str:
        params = model_data.get("params") or {}
        meta = model_data.get("meta") or {}
        capabilities = (meta.get("capabilities") or {}) if isinstance(meta, dict) else {}
        info = model_data.get("info") or {}
        info_params = (info.get("params") or {}) if isinstance(info, dict) else {}
        mode = (
            params.get("function_calling")
            if isinstance(params, dict) else None
        ) or (
            info_params.get("function_calling")
            if isinstance(info_params, dict) else None
        )
        if mode:
            return str(mode)
        return "supported" if isinstance(capabilities, dict) and capabilities.get("function_calling") else "none"

    @staticmethod
    def _request_tool_descriptions(
        body: Dict[str, Any], metadata: Optional[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Normalize tool IDs/schemas selected for this request from inlet data."""
        metadata = metadata or {}
        tool_ids = body.get("tool_ids") or metadata.get("tool_ids") or []
        if not tool_ids:
            tool_ids = (body.get("features") or {}).get("tool_ids") or []
        if not isinstance(tool_ids, list):
            tool_ids = [tool_ids]

        sources = body.get("tools") or metadata.get("tools") or []
        if not sources:
            sources = metadata.get("tool_schemas") or []
        if isinstance(sources, dict):
            sources = list(sources.values())

        tools: List[Dict[str, str]] = []
        for tool in sources:
            if not isinstance(tool, dict):
                continue
            spec = tool.get("function") or tool.get("spec") or tool
            if not isinstance(spec, dict):
                continue
            name = spec.get("name") or tool.get("name") or tool.get("tool_id")
            description = spec.get("description") or tool.get("description") or ""
            if name:
                tools.append({"name": str(name), "description": str(description)})

        known_ids = {tool["name"] for tool in tools}
        tools.extend(
            {"name": str(tool_id), "description": ""}
            for tool_id in tool_ids
            if tool_id and str(tool_id) not in known_ids
        )
        return tools

    async def _get_model_recommendation(
        self,
        body: Dict[str, Any],
        available_models: List[ModelInfo],
        user_message: str,
        is_voice: bool,
        metadata: Optional[Dict[str, Any]] = None,
        current_route: Optional[Dict[str, Any]] = None,
    ) -> RouterResponse:
        """Ask the configured Cloudflare structured decision model."""
        account_id = self.valves.cloudflare_account_id.strip()
        token = self.valves.cloudflare_api_token.strip()
        if self.valves.debug:
            logger.info(
                "Cloudflare configuration: account_id=%s, token_configured=%s, gateway_id=%s",
                account_id or "<empty>",
                bool(token),
                self.valves.cloudflare_gateway_id.strip() or "default",
            )
        if not account_id or not token:
            raise ValueError("Cloudflare account ID and API token must be configured")

        has_images = self._has_images(body.get("messages", []))
        decision_model = (
            self.valves.image_text_decision_model if has_images
            else self.valves.text_decision_model
        ).strip()
        candidates = [dict(model) for model in available_models]
        selected_tools = self._request_tool_descriptions(body, metadata)
        if len(candidates) == 1:
            return {
                "selected_model_id": candidates[0]["id"],
                "reasoning": "Only one eligible response model is available",
                "context_need": 0.0,
                "current_capacity": None,
            }
        state = {
            "conversation": [
                {
                    "role": message.get("role"),
                    "content": self._message_text_without_images(message.get("content", "")),
                }
                for message in body.get("messages", [])[-24:]
                if message.get("role") != "system"
            ],
            "interaction_mode": "voice" if is_voice else "text",
            "selected_tools": selected_tools,
        }
        candidate_ids = {}
        candidate_options = {}
        for index, candidate in enumerate(candidates):
            option = f"candidate_{index}"
            candidate_ids[option] = candidate["id"]
            candidate_text = f"{candidate['name']}: {candidate['description'] or ''}".strip()
            if candidate.get("tools"):
                candidate_text += "; available tools: " + ", ".join(candidate["tools"])
            capabilities = candidate.get("capabilities") or {}
            function_calling = candidate.get("function_calling", "none")
            if function_calling and function_calling != "none":
                candidate_text += f"; function calling: {function_calling}"
            elif capabilities.get("function_calling"):
                candidate_text += "; function calling: supported"
            candidate_options[option] = candidate_text
        questions = {
            "selected_model": {
                "type": "choice",
                "instructions": "Choose the best option for the user's request.",
                "criteria": candidate_options,
            },
            "context_need": {
                "type": "score",
                "instructions": "Estimate the context window needed for this conversation and task. Use the S/M/L context labels stated in model descriptions; do not parse or count exact tokens.",
                "criteria": [
                    "S: short, up to 100K tokens",
                    "M: medium, up to 250K tokens",
                    "L: long, up to 1M tokens",
                ],
            },
        }
        if decision_model not in {
            "typesafe/jev",
            "@cf/cloudflare/clef",
            "@cf/cloudflare/clef-flash",
        }:
            raise ValueError(f"Unsupported decision model: {decision_model}")
        is_clef = decision_model in {
            "@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"
        }
        input_data = {"state": state, "questions": questions}
        if is_clef:
            input_data["model"] = (
                "clef-flash" if decision_model.endswith("clef-flash") else "clef"
            )
            images = self._extract_clef_images(body.get("messages", []))
            if images:
                input_data["images"] = images
            elif has_images:
                raise ValueError("Clef needs embedded image bytes, but none were available from the request")

        base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
        if is_clef:
            url = f"{base_url}/{decision_model}"
        else:
            url = base_url
            input_data = {"model": decision_model, "input": input_data}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        headers["cf-aig-gateway-id"] = self.valves.cloudflare_gateway_id.strip() or "default"

        async with httpx.AsyncClient(
            timeout=self.valves.request_timeout_seconds
        ) as client:
            response = await client.post(url, headers=headers, json=input_data)
            response.raise_for_status()
            payload = response.json()
        if payload.get("success") is False or payload.get("errors"):
            raise RuntimeError(f"Cloudflare decision request failed: {payload.get('errors')}")
        result = payload
        for _ in range(5):
            if not isinstance(result, dict):
                break
            if isinstance(result.get("answers"), dict):
                break
            nested = result.get("result")
            if not isinstance(nested, dict):
                break
            result = nested

        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            if self.valves.debug:
                logger.debug(
                    "Cloudflare decision response has no answers object; top-level keys=%s",
                    list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
                )
            raise ValueError("Cloudflare decision response did not contain an answers object")

        answers = result["answers"]
        selection = answers.get("selected_model", {})
        selected_option = selection.get("choice")
        selected_id = candidate_ids.get(selected_option)
        if not selected_id:
            if self.valves.debug:
                logger.debug(
                    "Unknown decision choice=%r; allowed choices=%s; response answer keys=%s",
                    selected_option,
                    list(candidate_ids),
                    list(answers),
                )
            raise ValueError(f"Decision model returned unknown option {selected_option!r}")
        if self.valves.debug:
            logger.debug(
                "Decision choice=%s mapped to model_id=%s (confidence=%s)",
                selected_option,
                selected_id,
                selection.get("confidence"),
            )
        context = answers.get("context_need", {})
        if "score" not in context:
            raise ValueError("Decision response omitted the context_need score")
        current_capacity = None
        return {
            "selected_model_id": selected_id,
            "reasoning": (
                f"confidence={selection.get('confidence', 'unknown')}; "
                f"estimated_context_score={context.get('score', 'unknown')}"
            ),
            "context_need": float(context.get("score", 0)),
            "current_capacity": current_capacity,
        }

    def _extract_clef_images(self, messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not messages:
            return []
        images = []
        last_user_message = next(
            (message for message in reversed(messages) if message.get("role") == "user"),
            messages[-1],
        )
        for message in [last_user_message]:
            content = message.get("content", [])
            if isinstance(content, list):
                for item in content:
                    url = (
                        item.get("image_url", {}).get("url")
                        if item.get("type") == "image_url" else None
                    )
                    if url and url.startswith("data:image/") and "," in url:
                        header, encoded = url.split(",", 1)
                        images.append({
                            "content_type": header[5:].split(";", 1)[0],
                            "base64": encoded,
                        })
            for image in message.get("images", []):
                if isinstance(image, str) and image.startswith("data:image/") and "," in image:
                    header, encoded = image.split(",", 1)
                    images.append({
                        "content_type": header[5:].split(";", 1)[0],
                        "base64": encoded,
                    })
                elif isinstance(image, dict) and image.get("base64"):
                    images.append({
                        "content_type": image.get("content_type", "image/jpeg"),
                        "base64": image["base64"],
                    })
        return images[-4:]

    @staticmethod
    def _message_text_without_images(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        return [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]

    def _build_file_data(
        self, file_metadata: FileMetadataResponse, collection_name: str
    ) -> Dict[str, Any]:
        """
        Build file data structure for a single file in the INPUT format
        expected by get_sources_from_items().

        This creates the structure that Open WebUI's RAG system will process,
        NOT the final citation structure.

        RAG File Handling Flow:
        1. _get_files_from_collections: Retrieves files from model's knowledge collections
        2. _build_file_data: Formats each file with metadata (id, name, collection_name)
        3. _merge_files: Combines knowledge files with any existing request files
        4. _process_files_for_knowledge: Groups merged files by collection_name
        5. _build_collection_data: Creates collection structures with file_ids arrays
        6. Final structure placed in body["metadata"]["model"]["info"]["meta"]["knowledge"]

        This ensures ALL files from ALL collections are properly included for RAG retrieval.
        """
        file_id = file_metadata.id
        meta = file_metadata.meta or {}

        return {
            "type": "file",
            "id": file_id,
            "name": meta.get("name", file_id),
            "collection_name": collection_name,
            "legacy": False,
        }

    async def _get_files_from_collections(
        self, knowledge_collections: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Get files from knowledge collections and format for RAG retrieval.
        Returns INPUT format expected by get_sources_from_items().
        Handles multiple knowledge collection structure formats.
        """
        files_data: List[Dict[str, Any]] = []

        if self.valves.debug:
            logger.debug(
                f"Processing {len(knowledge_collections)} knowledge collections"
            )
            logger.debug(
                f"Raw knowledge_collections structure: {json.dumps(knowledge_collections, indent=2, default=str)}"
            )

        for collection in knowledge_collections:
            if not isinstance(collection, dict):
                if self.valves.debug:
                    logger.debug(f"Skipping non-dict collection: {type(collection)}")
                continue

            if self.valves.debug:
                logger.debug(
                    f"Processing collection: {json.dumps(collection, indent=2, default=str)}"
                )

            collection_id = collection.get("id")
            if not collection_id:
                if self.valves.debug:
                    logger.debug(f"Skipping collection with no ID: {collection}")
                continue

            file_ids = collection.get("data", {}).get("file_ids", [])
            structure_used = "data.file_ids"

            if not file_ids:
                file_ids = collection.get("file_ids", [])
                structure_used = "file_ids"

            if not file_ids:
                files = collection.get("files", [])
                if isinstance(files, list):
                    file_ids = [
                        f.get("id") if isinstance(f, dict) else f for f in files
                    ]
                    structure_used = "files"

            if self.valves.debug:
                logger.debug(
                    f"Collection '{collection_id}': found {len(file_ids)} file IDs using structure '{structure_used}' - {file_ids}"
                )

            for file_id in file_ids:
                try:
                    file_metadata = await Files.get_file_metadata_by_id(file_id)
                    if file_metadata:
                        if not any(f["id"] == file_metadata.id for f in files_data):
                            file_data = self._build_file_data(
                                file_metadata, collection_id
                            )
                            files_data.append(file_data)
                            if self.valves.debug:
                                logger.debug(
                                    f"  Added file {file_metadata.id} from collection {collection_id}"
                                )
                        else:
                            if self.valves.debug:
                                logger.debug(
                                    f"  File {file_metadata.id} already in files_data, skipping"
                                )
                    else:
                        logger.warning(f"File {file_id} not found in Files database")

                except Exception as e:
                    logger.error(
                        f"Error getting file {file_id} from collection {collection_id}: {str(e)}"
                    )

        if self.valves.debug:
            logger.debug(f"Total files collected: {len(files_data)}")
            logger.debug(f"File IDs: {[f['id'] for f in files_data]}")

        return files_data

    def _merge_files(
        self, existing_files: List[Dict[str, Any]], new_files: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge new files with existing files, avoiding duplicates"""
        merged = existing_files.copy() if existing_files else []
        existing_ids = {f["id"] for f in merged}

        for file_data in new_files:
            file_id = file_data["id"]
            if file_id in existing_ids:
                idx = next(i for i, f in enumerate(merged) if f["id"] == file_id)
                merged[idx].update(dict(file_data))
            else:
                merged.append(dict(file_data))
                existing_ids.add(file_id)

        return merged

    def _build_updated_body(
        self,
        original_body: Dict[str, Any],
        selected_model: ModelInfo,
        selected_model_full: Dict[str, Any],
        files_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build the updated request body with selected model and metadata"""

        new_body = original_body.copy()
        new_body["model"] = selected_model["id"]

        model_data = selected_model_full
        meta = model_data.get("meta", {})

        params = model_data.get("params", {})
        info_params = model_data.get("info", {}).get("params", {})

        has_native_fc = False
        if params.get("function_calling") == "native":
            has_native_fc = True
        elif (
            isinstance(info_params, dict)
            and info_params.get("function_calling") == "native"
        ):
            has_native_fc = True
        elif (
            hasattr(info_params, "model_dump")
            and info_params.model_dump().get("function_calling") == "native"
        ):
            has_native_fc = True
        elif isinstance(meta, dict):
            cap = meta.get("capabilities", {})
            if isinstance(cap, dict) and cap.get("function_calling"):
                has_native_fc = True

        new_body.setdefault("metadata", {})
        original_metadata = original_body.get("metadata", {})

        if has_native_fc:
            new_body.pop("files", None)
            if "files" in new_body["metadata"]:
                new_body["metadata"].pop("files", None)
            new_body["metadata"].setdefault("params", {})["function_calling"] = "native"

        source_filter_ids = original_metadata.get("filterIds", []) or []
        target_filter_ids = meta.get("filterIds", []) or []
        if not isinstance(source_filter_ids, list):
            source_filter_ids = [source_filter_ids]
        if not isinstance(target_filter_ids, list):
            target_filter_ids = [target_filter_ids]
        new_body["metadata"]["filterIds"] = list(
            dict.fromkeys(source_filter_ids + target_filter_ids)
        )

        # 1. Start with global features from request body
        global_features = (
            original_metadata.get("features") or original_body.get("features") or {}
        )
        p_features = {}
        p_features.update(global_features)

        default_tool_ids = []

        if isinstance(meta, dict):
            default_tool_ids.extend(meta.get("toolIds", []))
            if isinstance(meta.get("features"), dict):
                p_features.update(meta["features"])
            for f_id in meta.get("defaultFeatureIds", []):
                p_features[f_id] = True

        params = model_data.get("params", {})
        if isinstance(params, dict):
            default_tool_ids.extend(
                params.get("toolIds", []) or params.get("tools", [])
            )
            if isinstance(params.get("features"), dict):
                p_features.update(params["features"])
            for f_id in params.get("defaultFeatureIds", []):
                p_features[f_id] = True

        # Clean and deduplicate tool IDs
        clean_default_ids = []
        for t in default_tool_ids:
            if isinstance(t, str) and t.strip():
                clean_default_ids.append(t.strip())
            elif isinstance(t, dict) and "id" in t:
                clean_default_ids.append(str(t["id"]))

        global_tool_ids = (
            original_metadata.get("tool_ids") or original_body.get("tool_ids") or []
        )
        combined_tool_ids = list(set(global_tool_ids + clean_default_ids))

        new_body.pop("tool_ids", None)
        new_body["metadata"].pop("tool_ids", None)
        if combined_tool_ids:
            new_body["tool_ids"] = combined_tool_ids

        new_body["features"] = p_features
        new_body["metadata"]["features"] = p_features

        for key in [
            "user_id",
            "chat_id",
            "message_id",
            "session_id",
            "direct",
            "variables",
        ]:
            if key in original_metadata:
                new_body["metadata"][key] = original_metadata[key]

        new_body["metadata"]["model"] = self._build_model_metadata(
            selected_model, model_data, original_metadata.get("model", {})
        )
        source_system = (
            original_metadata.get("model", {}).get("info", {}).get("params", {}).get("system")
        )
        target_system = params.get("system")
        messages = [dict(message) for message in new_body.get("messages", [])]
        system_index = next(
            (index for index, message in enumerate(messages) if message.get("role") == "system"),
            None,
        )
        if system_index is None and isinstance(target_system, str) and target_system.strip():
            messages.insert(0, {"role": "system", "content": target_system})
        elif system_index is not None and messages[system_index].get("content") == source_system:
            if isinstance(target_system, str) and target_system.strip():
                messages[system_index]["content"] = target_system
            else:
                messages.pop(system_index)
        new_body["messages"] = messages
        model_knowledge = meta.get("knowledge", [])
        if model_knowledge:
            normalized_knowledge = []
            for item in model_knowledge:
                if not isinstance(item, dict):
                    continue
                if item.get("type") and item.get("id"):
                    normalized_knowledge.append(item)
                elif item.get("collection_name"):
                    normalized_knowledge.append(
                        {
                            "type": "collection",
                            "id": item["collection_name"],
                            "name": item.get("name", ""),
                        }
                    )
                else:
                    normalized_knowledge.append(item)

            if normalized_knowledge:
                new_body["metadata"]["model"].setdefault("info", {}).setdefault(
                    "meta", {}
                )["knowledge"] = normalized_knowledge

                if not has_native_fc:
                    collection_items = []
                    for knowledge_item in normalized_knowledge:
                        if isinstance(knowledge_item, dict) and knowledge_item.get(
                            "id"
                        ):
                            collection_items.append(
                                {
                                    "type": "collection",
                                    "id": knowledge_item["id"],
                                    "legacy": False,
                                    "collection_name": knowledge_item.get("name", "")
                                    or knowledge_item["id"],
                                }
                            )

                    new_body["files"] = collection_items

                    if self.valves.debug:
                        logger.debug(
                            f"Set body['files'] with {len(collection_items)} collection items for RAG"
                        )
                        logger.debug(
                            f"Collection items: {json.dumps(collection_items, indent=2, default=str)}"
                        )
                # else:
                #     new_body.pop("files", None)
                #     if self.valves.debug:
                #         logger.debug(
                #             "Skipped setting body['files'] for collection items because model has native tool calling"
                #         )

        elif files_data:
            if not has_native_fc:
                file_items = []
                for file_data in files_data:
                    file_items.append(
                        {
                            "type": "file",
                            "id": file_data.get("id"),
                            "name": file_data.get("name"),
                            "legacy": False,
                        }
                    )

                new_body["files"] = file_items
                if self.valves.debug:
                    logger.debug(f"Set body['files'] with {len(file_items)} file items")
            # else:
            #     new_body.pop("files", None)
            #     if self.valves.debug:
            #         logger.debug(
            #             "Skipped setting body['files'] for file items because model has native tool calling"
            #         )

        return new_body

    def _build_model_metadata(
        self,
        selected_model: ModelInfo,
        model_data: Dict[str, Any],
        original_model: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build model metadata structure"""
        meta = model_data.get("meta", {})

        updated_model = {
            "id": selected_model["id"],
            "name": selected_model["name"],
            "description": meta.get("description", ""),
            "info": {
                "id": selected_model["id"],
                "name": selected_model["name"],
                "base_model_id": model_data.get("base_model_id"),
            },
        }

        for field in ["object", "created", "owned_by", "preset", "actions"]:
            if field in original_model:
                updated_model[field] = original_model[field]

        if "info" in original_model:
            for field in [
                "user_id",
                "updated_at",
                "created_at",
                "access_control",
                "is_active",
            ]:
                if field in original_model["info"]:
                    updated_model["info"][field] = original_model["info"][field]

        if "info" in original_model and "meta" in original_model["info"]:
            updated_model["info"]["meta"] = {
                k: v
                for k, v in original_model["info"]["meta"].items()
                if k != "toolIds"
            }
            if meta.get("toolIds"):
                updated_model["info"]["meta"]["toolIds"] = meta["toolIds"]

        # Ensure capabilities and builtinTools from the selected model's meta are preserved
        if "capabilities" in meta:
            updated_model["info"].setdefault("meta", {})["capabilities"] = meta[
                "capabilities"
            ]
        if "builtinTools" in meta:
            updated_model["info"].setdefault("meta", {})["builtinTools"] = meta[
                "builtinTools"
            ]

        updated_model["info"]["params"] = model_data.get("params", {})

        return updated_model

    async def inlet(
        self,
        body: Dict[str, Any],
        __event_emitter__: Callable[[Any], Awaitable[None]],
        __metadata__: Optional[Dict[str, Any]] = None,
        __user__: Optional[Dict[str, Any]] = None,
        __model__: Optional[Dict[str, Any]] = None,
        __request__: Optional[Request] = None,
    ) -> Dict[str, Any]:
        """Main filter entry point"""
        self.__request__ = __request__
        self.__model__ = __model__
        self.__user__ = await Users.get_user_by_id(__user__["id"]) if __user__ else None

        models_state = getattr(self.__request__.app.state, "MODELS", {})

        messages = body.get("messages", [])
        metadata = __metadata__ or body.get("metadata", {})
        is_voice = self._is_voice_request(body, metadata)
        router_entry_selected = (
            bool(self.valves.router_entry_model_id)
            and body.get("model") == self.valves.router_entry_model_id
        )
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)
        routed_model_id = None
        route_state: Dict[str, Any] = {}
        route_marker_found = False
        route_started_here = False

        if not has_assistant_messages and not router_entry_selected:
            return body
        if router_entry_selected and not has_assistant_messages:
            route_started_here = True

        if has_assistant_messages:
            marker_pattern = (
                r"\n*(?:\u2063\u2063([\u200b\u200c]+)\u2063\u2063"
                r"|\u200B\u200C\u200D\u2060(.*?)\u200B\u200C\u200D\u2060)\n*"
            )
            for msg in reversed(messages):
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                match = re.search(marker_pattern, content)
                if not match:
                    continue
                route_marker_found = True
                marker_value = match.group(1) or match.group(2)
                route_state = self._decode_route_state(marker_value)
                routed_model_id = route_state.get("model_id")
                msg["content"] = re.sub(marker_pattern, "", content)
                if self.valves.debug:
                    logger.debug("Found and removed routing marker: %s", routed_model_id)
                break
            for msg in messages:
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                    msg["content"] = re.sub(marker_pattern, "", msg["content"])

        reroute_for_new_images = bool(
            route_marker_found
            and self._has_images(messages)
            and not route_state.get("has_images", False)
            and not route_state.get("vision_capable", False)
        )
        if reroute_for_new_images:
            routed_model_id = None

        if routed_model_id and route_state.get("voice", is_voice) == is_voice and not reroute_for_new_images:
            try:
                model_data = await self._get_full_model_data(routed_model_id)
                meta = model_data.get("meta", {})
                selected_model: ModelInfo = {
                    "id": routed_model_id,
                    "name": model_data.get("name", routed_model_id),
                    "description": meta.get("description", ""),
                    "vision_capable": is_vision_capable(model_data),
                    "capabilities": meta.get("capabilities", {}),
                    "tools": self._model_tool_descriptions(model_data),
                    "function_calling": self._model_function_calling(model_data),
                    "updated_at": model_data.get("updated_at"),
                    "created_at": model_data.get("created_at"),
                }
                files_data = list(body.get("files", []))
                knowledge = meta.get("knowledge", [])
                if isinstance(knowledge, list) and knowledge:
                    knowledge_files = await self._get_files_from_collections(knowledge)
                    files_data = self._merge_files(files_data, knowledge_files)
                new_body = self._build_updated_body(
                    body, selected_model, model_data, files_data
                )
                source_owner = body.get("metadata", {}).get("model", {}).get("owned_by")
                target_owner = model_data.get("owned_by")
                if source_owner != target_owner:
                    if target_owner == "ollama":
                        new_body = convert_payload_openai_to_ollama(new_body)
                    else:
                        new_body = clean_ollama_params(new_body)
                return new_body
            except Exception as exc:
                logger.error("Could not restore persisted route: %s", exc, exc_info=True)
                return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

        if route_marker_found and not routed_model_id:
            has_assistant_messages = False
        if route_marker_found and route_state.get("router_entry"):
            route_started_here = True
        router_entry_selected = router_entry_selected or route_started_here

        if has_assistant_messages and not router_entry_selected and not route_marker_found:
            return body

        has_images = self._has_images(messages)

        try:
            if not models_state:
                logger.warning("No models found in app.state.MODELS")
                return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

            available_models = await self._get_available_models(filter_vision=has_images)

            if not available_models:
                logger.warning("No valid models found for routing")
                return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

            if self.valves.status:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Analyzing request to select best model...",
                            "done": False,
                        },
                    }
                )

            user_message = get_last_user_message(messages)
            result = await self._get_model_recommendation(
                body,
                available_models,
                user_message if user_message else "",
                is_voice,
                {
                    **metadata,
                    "tool_ids": metadata.get("tool_ids") or body.get("tool_ids"),
                },
                {
                    "id": route_state.get("model_id"),
                    "description": route_state.get("description", ""),
                    "context_profile": route_state.get("context_profile", ""),
                    "voice": route_state.get("voice", is_voice),
                } if route_marker_found else None,
            )

            self._routed_model_id = result["selected_model_id"]

            logger.info("=" * 80)
            logger.info("ROUTING DECISION MADE")
            logger.info(f"  Original body['model']: {body.get('model')}")
            logger.info(f"  Selected model: {result['selected_model_id']}")
            logger.info(f"  Reasoning: {result['reasoning']}")
            logger.info("=" * 80)

            if self.valves.show_reasoning:
                reasoning_message = (
                    f"<details>\n<summary>Model Selection</summary>\n"
                    f"Selected Model: {result['selected_model_id']}\n\n"
                    f"Reasoning: {result['reasoning']}\n\n---\n\n</details>"
                )
                await __event_emitter__(
                    {
                        "type": "message",
                        "data": {"content": reasoning_message},
                    }
                )

            selected_model: ModelInfo | None = next(
                (m for m in available_models if m["id"] == result["selected_model_id"]),
                None,
            )
            if not selected_model:
                logger.error(
                    f"Selected model {result['selected_model_id']} not found in available models"
                )
                return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

            selected_model_full = None
            if selected_model["id"] in models_state:
                selected_model_full = await self._get_full_model_data(selected_model["id"])

            if not selected_model_full:
                logger.warning(
                    f"Could not find full model data for {selected_model['id']}"
                )
                return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

            if self.valves.debug:
                logger.debug("=" * 80)
                logger.debug("STARTING FILE COLLECTION PROCESS")
                logger.debug("=" * 80)

            files_data: List[Dict[str, Any]] = []

            original_files = body.get("files", [])
            if original_files:
                if self.valves.debug:
                    logger.debug(
                        f"Step 1: Original request body has {len(original_files)} files"
                    )
                    logger.debug(
                        f"Original files: {json.dumps(original_files, indent=2, default=str)}"
                    )
                files_data.extend(original_files)
            else:
                if self.valves.debug:
                    logger.debug("Step 1: No files in original request body")

            original_metadata = body.get("metadata", {})
            original_model_info = original_metadata.get("model", {}).get("info", {})
            original_knowledge = original_model_info.get("meta", {}).get(
                "knowledge", []
            )

            if isinstance(original_knowledge, list) and original_knowledge:
                if self.valves.debug:
                    logger.debug(
                        f"Step 2: Original source model has {len(original_knowledge)} knowledge collections"
                    )

                original_knowledge_files = await self._get_files_from_collections(
                    original_knowledge
                )

                if original_knowledge_files:
                    if self.valves.debug:
                        logger.debug(
                            f"Step 2: Retrieved {len(original_knowledge_files)} files from source model's knowledge collections"
                        )

                    files_data = self._merge_files(files_data, original_knowledge_files)

                    if self.valves.debug:
                        logger.debug(
                            f"Step 2: After merging source model knowledge: {len(files_data)} total files"
                        )
                        logger.debug(
                            f"Step 2: Current file IDs: {[f['id'] for f in files_data]}"
                        )
            else:
                if self.valves.debug:
                    logger.debug("Step 2: No knowledge collections in source model")

            model_data = selected_model_full
            meta = model_data.get("meta", {})
            knowledge = meta.get("knowledge")
            if not isinstance(knowledge, list):
                knowledge = []

            self._routed_model_knowledge = knowledge
            self._routed_model_tools = meta.get("toolIds", [])

            if self.valves.debug:
                logger.debug(
                    f"Step 3: Selected target model '{selected_model['id']}' has {len(knowledge)} knowledge collections"
                )
                logger.debug(
                    f"Step 3: Target model knowledge structure: {json.dumps(knowledge, indent=2, default=str)}"
                )

            if isinstance(knowledge, list) and knowledge:
                knowledge_files = await self._get_files_from_collections(knowledge)

                if self.valves.debug:
                    logger.debug(
                        f"Step 3: Retrieved {len(knowledge_files)} files from target model's knowledge collections"
                    )
                    logger.debug(
                        f"Step 3: Target model file IDs: {[f['id'] for f in knowledge_files]}"
                    )

                files_data = self._merge_files(files_data, knowledge_files)

                if self.valves.debug:
                    logger.debug(
                        f"Step 3: After final merge: {len(files_data)} total files"
                    )
                    logger.debug(
                        f"Step 3: Final merged file IDs: {[f['id'] for f in files_data]}"
                    )
            else:
                if self.valves.debug:
                    logger.debug(
                        "Step 3: No knowledge collections found on target model"
                    )

            new_body = self._build_updated_body(
                body, selected_model, selected_model_full, files_data
            )

            # Add newlines around marker so it doesn't stick to LLM response text
            route_marker = self._encode_route_state({
                "model_id": selected_model["id"],
                "voice": is_voice,
                "description": selected_model["description"],
                "context_profile": context_profile_from_score(result["context_need"]),
                "router_entry": router_entry_selected,
                "has_images": has_images,
                "vision_capable": selected_model["vision_capable"],
            })
            hidden_marker = f"\n\n{route_marker}\n\n"

            await __event_emitter__(
                {
                    "type": "message",
                    "data": {"content": hidden_marker},
                }
            )

            if self.valves.debug:
                logger.debug(
                    f"Emitted invisible routing marker for model: {selected_model['id']}"
                )

            if self.valves.debug:
                logger.debug("=" * 80)
                logger.debug("FINAL BODY BEING RETURNED:")
                logger.debug(f"  Model: {new_body.get('model')}")
                logger.debug(
                    f"  Persisted model ID in metadata.variables: {selected_model['id']}"
                )
                body_files = new_body.get("files", [])
                logger.debug(f"  body['files'] count: {len(body_files)}")
                logger.debug(
                    f"  body['files'] structure: {json.dumps(body_files, indent=2, default=str)}"
                )
                meta_dict = (
                    new_body.get("metadata", {})
                    .get("model", {})
                    .get("info", {})
                    .get("meta", {})
                )
                knowledge_in_body = meta_dict.get("knowledge") if isinstance(meta_dict, dict) else []
                if not isinstance(knowledge_in_body, list):
                    knowledge_in_body = []

                logger.debug(f"  Knowledge collections count: {len(knowledge_in_body)}")
                logger.debug(
                    f"  Knowledge structure: {json.dumps(knowledge_in_body, indent=2, default=str)}"
                )
                logger.debug("=" * 80)

            source_owned_by = body.get("metadata", {}).get("model", {}).get("owned_by")
            target_owned_by = model_data.get("owned_by")

            if source_owned_by != target_owned_by:
                if target_owned_by == "ollama":
                    new_body = convert_payload_openai_to_ollama(new_body)
                else:
                    new_body = clean_ollama_params(new_body)

            if self.valves.status:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": f"Selected: {selected_model['name']}",
                            "done": True,
                        },
                    }
                )

            logger.info("=" * 80)
            logger.info("RETURNING ROUTED BODY")
            logger.info(f"  new_body['model']: {new_body.get('model')}")
            logger.info(f"  Tool IDs: {new_body.get('tool_ids', [])}")
            logger.info(f"  Knowledge collections: {len(self._routed_model_knowledge)}")
            logger.info("=" * 80)

            return new_body

        except Exception as e:
            logger.error("Error in semantic routing: %s", str(e), exc_info=True)
            if self.valves.status:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Error during model selection",
                            "done": True,
                        },
                    }
                )
            return await self._apply_fallback(body, models_state, __event_emitter__, is_voice)

    async def _apply_fallback(
        self,
        body: Dict[str, Any],
        models_state: Dict[str, Any],
        event_emitter: Callable[[Any], Awaitable[None]],
        is_voice: bool,
    ) -> Dict[str, Any]:
        fallback_id = self.valves.fallback_model_id.strip()
        if fallback_id and fallback_id in models_state:
            fallback = await self._get_full_model_data(fallback_id)
            fallback_meta = fallback.get("meta", {})
            selected_fallback: ModelInfo = {
                "id": fallback_id,
                "name": fallback.get("name", fallback_id),
                "description": fallback_meta.get("description", ""),
                "vision_capable": is_vision_capable(fallback),
                "capabilities": fallback_meta.get("capabilities", {}),
                "tools": self._model_tool_descriptions(fallback),
                "function_calling": self._model_function_calling(fallback),
                "created_at": fallback.get("created_at"),
                "updated_at": fallback.get("updated_at"),
            }
            body = self._build_updated_body(
                body, selected_fallback, fallback, body.get("files", [])
            )
            marker = self._encode_route_state({
                "model_id": fallback_id,
                "voice": is_voice,
                "description": selected_fallback["description"],
                "has_images": self._has_images(body.get("messages", [])),
                "vision_capable": selected_fallback["vision_capable"],
                "router_entry": True,
            })
            await event_emitter({
                "type": "message",
                "data": {"content": f"\n\n{marker}\n\n"},
            })
            metadata = body.setdefault("metadata", {})
            metadata["filterIds"] = list(
            dict.fromkeys((metadata.get("filterIds") or []) + [self.valves.filter_id])
            )
            logger.info("Routing fallback selected: %s", fallback_id)
        elif fallback_id:
            logger.warning("Configured fallback model %s is not available", fallback_id)
        return body
