try:
    from langchain_google_vertexai import ChatVertexAI
    print("ChatVertexAI imported!")
except Exception as e:
    import traceback
    traceback.print_exc()
