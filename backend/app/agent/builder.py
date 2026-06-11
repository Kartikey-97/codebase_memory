from __future__ import annotations

from typing import Any

import vertexai
from vertexai.preview import reasoning_engines

from app.agent.tools import build_tool_callables
from app.config import get_settings

DEFAULT_AGENT_NAME = "CodebaseMemoryAgent"

SYSTEM_INSTRUCTION = (
    "You are Codebase Memory, a proactive developer intelligence agent. "
    "Use tools to inspect repository chunks, file metadata, and relationships. "
    "Do not rely on conversational memory; treat every request as stateless and fetch context from tools."
)


class AgentBuilderError(Exception):
    pass

def initialize_vertex_ai() -> None:
    """Initialize Vertex AI strictly from environment-backed settings."""
    settings = get_settings()
    if not settings.vertex_ai_project.strip():
        raise AgentBuilderError("VERTEX_AI_PROJECT is required.")
    if not settings.vertex_ai_location.strip():
        raise AgentBuilderError("VERTEX_AI_LOCATION is required.")

    init_kwargs: dict[str, Any] = {
        "project": settings.vertex_ai_project,
        "location": settings.vertex_ai_location,
    }
    if settings.vertex_ai_staging_bucket.strip():
        init_kwargs["staging_bucket"] = settings.vertex_ai_staging_bucket

    vertexai.init(**init_kwargs)

def get_ingest_model() -> Any:
    from vertexai.generative_models import GenerativeModel
    return GenerativeModel(get_settings().vertex_ai_model_ingest)

def get_chat_model() -> Any:
    from vertexai.generative_models import GenerativeModel
    return GenerativeModel(get_settings().vertex_ai_model_chat)

from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

def build_local_agent(repo_id: str) -> Any:
    """Build a local Langchain agent with typed Python tools for a single request scope."""
    initialize_vertex_ai()
    settings = get_settings()
    tool_callables, _ = build_tool_callables(repo_id=repo_id)

    llm = ChatVertexAI(model=settings.vertex_ai_model_ingest, temperature=0.1)
    
    agent = create_react_agent(llm, tools=tool_callables, state_modifier=SYSTEM_INSTRUCTION)
    return agent


def deploy_reasoning_engine(
    repo_id: str,
    *,
    display_name: str = "codebase-memory-agent",
) -> reasoning_engines.ReasoningEngine:
    """Wrap the local agent in a deployable ReasoningEngine for Agent Engine."""
    local_agent = build_local_agent(repo_id=repo_id)

    requirements = [
        "google-cloud-aiplatform[reasoningengine,langchain]>=1.156.0,<2.0",
        "httpx>=0.28,<1.0",
        "pydantic>=2.11,<3.0",
    ]

    return reasoning_engines.ReasoningEngine.create(
        local_agent,
        display_name=display_name,
        requirements=requirements,
    )


def query_agent_once(repo_id: str, message: str) -> dict[str, Any]:
    """
    Run a stateless single request against a fresh local agent.

    A new agent instance is created every call to avoid retaining in-process memory.
    """
    local_agent = build_local_agent(repo_id=repo_id)

    result = local_agent.invoke({"messages": [("user", message)]})
    last_msg = result["messages"][-1]
    response = last_msg.content

    return {"output": response}


CHAT_SYSTEM_INSTRUCTION = (
    "You are Codebase Memory, an expert code assistant for an indexed repository. "
    "You help developers understand their codebase by answering questions with "
    "precise, referenced answers.\n\n"
    "RULES FOR TOOL ROUTING:\n"
    "1. OVERVIEW: ALWAYS call get_repo_manifest() FIRST when asked about the repository's overall purpose, architecture, or tech stack.\n"
    "2. GRAPH-RAG (SUBSYSTEMS): When asked 'How does X work?', 'What is the flow of Y?', use find_subsystem_entrypoint() to find the root, then call analyze_subsystem(entry_file, max_depth=2) to trace the architecture.\n"
    "3. GRAPH-RAG (IMPACT): When asked 'What happens if I change X?', 'Is it safe to modify Y?', or to 'Analyze blast radius', call impact_analysis(file_path) to predict severity and trace dependencies safely.\n"
    "4. SYMBOL SEARCH: Call search_symbols() to instantly find where a specific class or function is defined.\n"
    "5. FILE API: Call get_file_skeleton() to inspect the structural API (functions, classes, imports) of a single file.\n"
    "6. FALLBACK (VECTOR SEARCH): Call search_codebase() ONLY to retrieve exact implementation details or raw strings after you have structural context.\n"
    "7. Do not fabricate code or file paths that were not returned by your tools.\n"
    "8. Keep answers concise but thorough. Developers value precision over verbosity."
)


def build_chat_agent(
    repo_id: str,
) -> tuple[Any, "CodebaseAgentTools"]:
    """Build a chat agent and return (agent, tools_instance).

    The tools instance is returned so the caller can read
    tools_instance.last_searched_paths after the query completes.
    """
    from app.agent.tools import CodebaseAgentTools  # noqa: F811 — type hint

    initialize_vertex_ai()
    settings = get_settings()
    tool_callables, tools_instance = build_tool_callables(repo_id=repo_id)

    llm = ChatVertexAI(model=settings.vertex_ai_model_chat, temperature=0.2)
    agent = create_react_agent(llm, tools=tool_callables, state_modifier=CHAT_SYSTEM_INSTRUCTION)
    
    return agent, tools_instance
