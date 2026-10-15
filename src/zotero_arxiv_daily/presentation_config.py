"""Parse independent email presentation switches at the configuration boundary."""

from dataclasses import dataclass

from omegaconf import DictConfig


class InvalidPresentationSwitchError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(f"{key} must be true or false")


def _enabled(config: DictConfig, feature: str) -> bool:
    """Accept YAML booleans or their string equivalents, never string truthiness."""
    # Composed production config supplies true; sparse legacy callers stay opt-in.
    value = config.get("email", {}).get(feature, {}).get("enabled", False)
    match value:
        case bool() as enabled:
            return enabled
        case str() as text if text.strip().lower() in {"true", "false"}:
            return text.strip().lower() == "true"
        case _:  # Untrusted configuration, not a closed domain variant.
            raise InvalidPresentationSwitchError(f"email.{feature}.enabled")


@dataclass(frozen=True, slots=True)
class PresentationOptions:
    bilingual: bool
    figures: bool

    @classmethod
    def from_config(cls, config: DictConfig) -> "PresentationOptions":
        return cls(
            bilingual=_enabled(config, "bilingual"),
            figures=_enabled(config, "figures"),
        )
