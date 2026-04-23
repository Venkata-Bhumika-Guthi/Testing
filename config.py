from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "LLM Health Guardian"
    env: str = "local"
    version: str = "0.1.0"

    # GCP / Vertex AI
    gcp_project_id: str = "llm-health-guardian"
    gcp_location: str = "us-central1"  
    gemini_model_name: str = "gemini-2.5-flash"  

    class Config:
        env_file = ".env"
        env_prefix = "LHG_"


settings = Settings()
