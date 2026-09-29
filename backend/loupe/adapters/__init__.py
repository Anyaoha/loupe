from loupe.adapters.base import SourceAdapter
from loupe.adapters.github import GitHubAdapter
from loupe.config import Settings

SUPPORTED_SOURCES = ("github",)


def build_adapter(source: str, settings: Settings) -> SourceAdapter:
    if source == "github":
        token = settings.github_token.get_secret_value() if settings.github_token else ""
        return GitHubAdapter(token=token, graphql_url=settings.github_graphql_url, timeout=settings.github_timeout_seconds)
    raise ValueError(f"unsupported source: {source}")
