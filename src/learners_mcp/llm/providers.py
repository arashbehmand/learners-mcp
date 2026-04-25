from .profiles import Profile


def supports_anthropic_blocks(model: str) -> bool:
    if model.startswith("anthropic/"):
        return True
    if model.startswith("claude-"):
        return True
    if model.startswith("bedrock/anthropic.") or (
        model.startswith("bedrock/") and "claude" in model
    ):
        return True
    if model.startswith("vertex_ai/claude"):
        return True
    if model.startswith("openrouter/anthropic/"):
        return True
    return False


def effective_cache_mode(profile: Profile) -> bool:
    if profile.prompt_cache == "on":
        return True
    if profile.prompt_cache == "off":
        return False
    return supports_anthropic_blocks(profile.model)
