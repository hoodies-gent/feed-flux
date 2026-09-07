from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Config


def get_llm(provider: str | None = None, **overrides) -> BaseChatModel:
    provider = (provider or Config.LLM_PROVIDER).lower()

    if provider == "deepseek":
        from langchain_openai import ChatOpenAI

        if not Config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is missing.")
        return ChatOpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
            model=Config.DEEPSEEK_MODEL_NAME,
            **overrides,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not Config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is missing.")
        return ChatGoogleGenerativeAI(
            model=Config.GEMINI_MODEL_NAME,
            google_api_key=Config.GOOGLE_API_KEY,
            **overrides,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'. Expected 'deepseek' or 'gemini'.")
