from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Config


def get_llm(
    provider: str | None = None,
    model_name: str | None = None,
    **overrides,
) -> BaseChatModel:
    provider = (provider or Config.LLM_PROVIDER).lower()
    model_options = dict(overrides)
    if Config.LLM_TEMPERATURE is not None:
        model_options.setdefault("temperature", Config.LLM_TEMPERATURE)

    if provider == "deepseek":
        from langchain_openai import ChatOpenAI

        if not Config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is missing.")
        return ChatOpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
            model=model_name or Config.DEEPSEEK_MODEL_NAME,
            **model_options,
        )

    if provider == "glm":
        from langchain_openai import ChatOpenAI

        if not Config.GLM_API_KEY:
            raise ValueError("GLM_API_KEY is missing.")
        return ChatOpenAI(
            api_key=Config.GLM_API_KEY,
            base_url=Config.GLM_BASE_URL,
            model=model_name or Config.GLM_MODEL_NAME,
            **model_options,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not Config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is missing.")
        return ChatGoogleGenerativeAI(
            model=model_name or Config.GEMINI_MODEL_NAME,
            google_api_key=Config.GOOGLE_API_KEY,
            **model_options,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. Expected 'deepseek', 'glm', or 'gemini'."
    )
