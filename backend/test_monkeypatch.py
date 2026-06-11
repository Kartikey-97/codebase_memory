# MUST BE AT THE VERY TOP
try:
    import pydantic.v1.fields
    import pydantic.v1.validators
    original_find_validators = pydantic.v1.validators.find_validators
    def safe_find_validators(type_, config):
        if type(type_).__name__ == "TypeAliasType":
            return []
        return original_find_validators(type_, config)
    pydantic.v1.validators.find_validators = safe_find_validators
    pydantic.v1.fields.find_validators = safe_find_validators
except ImportError:
    pass

import asyncio
from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from vertexai.generative_models import GenerativeModel, Tool, FunctionDeclaration
from langchain_google_vertexai import ChatVertexAI

async def main():
    initialize_vertex_ai()
    settings = get_settings()
    llm = ChatVertexAI(model=settings.vertex_ai_model_ingest, temperature=0.1)
    print("ChatVertexAI initialized successfully with monkey patch!")

asyncio.run(main())
