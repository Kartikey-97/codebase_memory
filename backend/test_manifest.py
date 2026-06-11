import asyncio
from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from vertexai.generative_models import GenerativeModel

async def main():
    initialize_vertex_ai()
    model = GenerativeModel("gemini-1.5-flash")
    print(model.generate_content("Say hello").text)

asyncio.run(main())
