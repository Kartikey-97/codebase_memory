import asyncio
from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from vertexai.generative_models import GenerativeModel

async def main():
    initialize_vertex_ai()
    settings = get_settings()

    model = GenerativeModel(
        model_name=settings.vertex_ai_model_ingest
    )
    
    response = model.generate_content("Say hello")
    print(response.text)

asyncio.run(main())
