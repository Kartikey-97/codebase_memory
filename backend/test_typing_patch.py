import sys
import typing_extensions

if hasattr(typing_extensions, "TypeAliasType"):
    del typing_extensions.TypeAliasType

from langchain_google_vertexai import ChatVertexAI
print("Success without TypeAliasType!")
