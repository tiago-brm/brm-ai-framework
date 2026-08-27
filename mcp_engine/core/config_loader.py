from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from mcp_engine.core.models import ClientConfig, RuleSet, SkillSet

ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigLoadError(Exception):
    def __init__(self, client_id: str, path: Path, cause: Exception) -> None:
        super().__init__(f"failed to load config for {client_id!r} from {path}: {cause}")
        self.client_id = client_id
        self.path = path
        self.__cause__ = cause


def _load(config_root: Path, client_id: str, filename: str, model: type[ModelT]) -> ModelT:
    path = config_root / client_id / "config" / filename

    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise ConfigLoadError(client_id, path, exc) from exc

    try:
        config = model.model_validate(data)
    except Exception as exc:
        raise ConfigLoadError(client_id, path, exc) from exc

    file_client_id = config.client_id  # type: ignore[attr-defined]
    if file_client_id != client_id:
        cause = ValueError(
            f"client_id in file ({file_client_id!r}) does not match "
            f"requested client_id ({client_id!r})"
        )
        raise ConfigLoadError(client_id, path, cause) from cause

    return config


class FileSystemRuleProvider:
    def __init__(self, config_root: Path) -> None:
        self._config_root = Path(config_root)

    def get_rules(self, client_id: str) -> RuleSet:
        return _load(self._config_root, client_id, "rules.yaml", RuleSet)


class FileSystemSkillProvider:
    def __init__(self, config_root: Path) -> None:
        self._config_root = Path(config_root)

    def get_skills(self, client_id: str) -> SkillSet:
        return _load(self._config_root, client_id, "skills.yaml", SkillSet)


class FileSystemClientConfigProvider:
    def __init__(self, config_root: Path) -> None:
        self._config_root = Path(config_root)

    def get_client_config(self, client_id: str) -> ClientConfig:
        return _load(self._config_root, client_id, "client_config.yaml", ClientConfig)
