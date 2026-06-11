import asyncio
from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from vertexai.generative_models import GenerativeModel, Tool, FunctionDeclaration

async def main():
    initialize_vertex_ai()
    settings = get_settings()

    write_insight_func = FunctionDeclaration(
        name="write_insight",
        description="Persist an insight document for this repo.",
        parameters={
            "type": "object",
            "properties": {
                "insight": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "severity": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["type", "severity", "title", "description"]
                }
            },
            "required": ["insight"]
        }
    )
    
    insight_tool = Tool(function_declarations=[write_insight_func])

    model = GenerativeModel(
        model_name=settings.vertex_ai_model_ingest,
        tools=[insight_tool]
    )
    
    response = model.generate_content("Generate a dummy insight using the tool.")
    print(response)

asyncio.run(main())
