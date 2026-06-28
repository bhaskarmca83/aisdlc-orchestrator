"""sdlc_orchestrator/providers/provider_factory.py
LLM provider abstraction — Anthropic (direct) + Bedrock + Vertex AI.
ANTHROPIC_API_KEY in env enables direct Claude as universal fallback.
"""
import os
from enum import Enum
from dataclasses import dataclass

class AgentRole(str, Enum):
    CONFLUENCE     = "confluence"
    STORY          = "story"
    DESIGN         = "design"
    IMPLEMENTATION = "implementation"
    TEST           = "test"
    REVIEW         = "review"
    DEPLOY         = "deploy"
    E2E            = "e2e"

class Provider(str, Enum):
    ANTHROPIC = "anthropic"   # direct Anthropic API
    BEDROCK   = "bedrock"
    VERTEX    = "vertex"
    OLLAMA    = "ollama"

@dataclass
class ModelConfig:
    provider: Provider
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    fallback_provider: Provider = Provider.ANTHROPIC
    fallback_model: str = "claude-3-5-sonnet-20241022"

AGENT_MODEL_MAP = {
    AgentRole.CONFLUENCE:     ModelConfig(Provider.BEDROCK, "anthropic.claude-3-5-sonnet-20241022-v2:0", temperature=0.0),
    AgentRole.STORY:          ModelConfig(Provider.BEDROCK, "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    AgentRole.DESIGN:         ModelConfig(Provider.BEDROCK, "anthropic.claude-3-5-sonnet-20241022-v2:0", temperature=0.2),
    AgentRole.IMPLEMENTATION: ModelConfig(Provider.VERTEX,  "gemini-1.5-pro-002",   fallback_provider=Provider.ANTHROPIC, fallback_model="claude-3-5-sonnet-20241022"),
    AgentRole.TEST:           ModelConfig(Provider.VERTEX,  "gemini-1.5-flash-002", fallback_provider=Provider.ANTHROPIC, fallback_model="claude-3-haiku-20240307"),
    AgentRole.REVIEW:         ModelConfig(Provider.BEDROCK, "anthropic.claude-3-5-sonnet-20241022-v2:0", temperature=0.0),
    AgentRole.DEPLOY:         ModelConfig(Provider.BEDROCK, "anthropic.claude-3-haiku-20240307-v1:0",    temperature=0.0),
    AgentRole.E2E:            ModelConfig(Provider.VERTEX,  "gemini-1.5-pro-002",   fallback_provider=Provider.ANTHROPIC, fallback_model="claude-3-5-sonnet-20241022"),
}

# Priority: OLLAMA_MODEL > ANTHROPIC_API_KEY > cloud providers
def _use_ollama_direct() -> bool:
    return bool(os.environ.get("OLLAMA_MODEL"))

def _use_anthropic_direct() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))

class ProviderFactory:
    @staticmethod
    def get_model(role: AgentRole):
        if _use_ollama_direct():
            return ProviderFactory._build(
                Provider.OLLAMA,
                os.environ["OLLAMA_MODEL"],
                AGENT_MODEL_MAP[role].temperature,
                AGENT_MODEL_MAP[role].max_tokens,
            )
        if _use_anthropic_direct():
            return ProviderFactory._build_anthropic(
                AGENT_MODEL_MAP[role].fallback_model,
                AGENT_MODEL_MAP[role].temperature,
                AGENT_MODEL_MAP[role].max_tokens,
            )
        config   = AGENT_MODEL_MAP[role]
        primary  = ProviderFactory._build(config.provider,  config.model,          config.temperature, config.max_tokens)
        fallback = ProviderFactory._build(config.fallback_provider, config.fallback_model, config.temperature, config.max_tokens)
        return primary.with_fallbacks([fallback])

    @staticmethod
    def _build_anthropic(model: str, temperature: float, max_tokens: int):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )

    @staticmethod
    def _build(provider: Provider, model: str, temperature: float, max_tokens: int):
        match provider:
            case Provider.ANTHROPIC:
                return ProviderFactory._build_anthropic(model, temperature, max_tokens)
            case Provider.BEDROCK:
                from langchain_aws import ChatBedrockConverse
                return ChatBedrockConverse(model=model, temperature=temperature, max_tokens=max_tokens,
                                          region_name=os.environ.get("AWS_REGION", "us-east-1"))
            case Provider.VERTEX:
                from langchain_google_vertexai import ChatVertexAI
                return ChatVertexAI(model=model, temperature=temperature, max_output_tokens=max_tokens,
                                    project=os.environ["GCP_PROJECT_ID"])
            case Provider.OLLAMA:
                from langchain_ollama import ChatOllama
                base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
                return ChatOllama(model=model, temperature=temperature, base_url=base_url)
