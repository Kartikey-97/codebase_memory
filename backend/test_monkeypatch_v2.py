try:
    import pydantic.v1.class_validators
    original_prepare_validator = pydantic.v1.class_validators._prepare_validator
    def safe_prepare_validator(*args, **kwargs):
        kwargs['allow_reuse'] = True
        return original_prepare_validator(*args, **kwargs)
    pydantic.v1.class_validators._prepare_validator = safe_prepare_validator

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

from langchain_google_vertexai import ChatVertexAI
print("Success!")
