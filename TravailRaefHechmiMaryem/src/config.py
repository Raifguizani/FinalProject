from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # === Neo4j Aura Cloud ===
    neo4j_uri:      str = "bolt://127.0.0.1:7687"
    neo4j_user:     str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # === Claude API (LLM principal) ===
    anthropic_api_key: str = ""

    # === Modèles Claude ===
    claude_fast_model: str  = "claude-haiku-4-5-20251001"   # classification, extraction
    claude_smart_model: str = "claude-sonnet-4-6"           # analyse marketing, pitchs

    # === Serper (recherche web) ===
    serper_api_key: str = ""

    # === Ollama (embeddings uniquement) ===
    ollama_base_url:   str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"

    # === Pipeline ===
    request_delay_seconds:       int   = 2
    embedding_confidence_threshold: float = 0.65
    llm_confidence_threshold:    float = 0.50
    scraping_concurrency:        int   = 3    # nombre d'entreprises scrapées en parallèle

    class Config:
        env_file = ".env"


settings = Settings()
