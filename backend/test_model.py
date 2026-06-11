from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from vertexai.generative_models import GenerativeModel

initialize_vertex_ai()
model = GenerativeModel("gemini-1.5-flash")
try:
    resp = model.generate_content("hello")
    print("SUCCESS: ", resp.text)
except Exception as e:
    print("FAILED: ", e)
