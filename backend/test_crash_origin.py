import traceback
try:
    print("Importing GenerativeModel...")
    from vertexai.generative_models import GenerativeModel
    print("Imported GenerativeModel!")
except Exception:
    traceback.print_exc()

try:
    print("Importing Tool...")
    from vertexai.generative_models import Tool
    print("Imported Tool!")
except Exception:
    traceback.print_exc()

try:
    print("Importing FunctionDeclaration...")
    from vertexai.generative_models import FunctionDeclaration
    print("Imported FunctionDeclaration!")
except Exception:
    traceback.print_exc()

try:
    print("Instantiating FunctionDeclaration...")
    func = FunctionDeclaration(
        name="test",
        description="test",
        parameters={"type": "object", "properties": {}}
    )
    print("Instantiated FunctionDeclaration!")
except Exception:
    traceback.print_exc()

try:
    print("Instantiating Tool...")
    tool = Tool(function_declarations=[func])
    print("Instantiated Tool!")
except Exception:
    traceback.print_exc()
