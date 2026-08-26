from pathlib import Path

import pytest

from mcp_engine.core.config_loader import (
    ConfigLoadError,
    FileSystemRuleProvider,
    FileSystemSkillProvider,
)
from mcp_engine.core.contracts import RuleProvider, SkillProvider
from mcp_engine.core.models import HttpMethod, RuleEffect, RuleSet, SkillSet


class TestFileSystemRuleProvider:
    def test_load_valid_rules_yaml(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text(
            """\
schema_version: 1
client_id: acme
rules:
  - id: max-valor
    description: Blocks requests above 10000
    condition: { ">": [{"var": "valor"}, 10000] }
    effect: deny
    message: valor acima do limite
"""
        )

        provider = FileSystemRuleProvider(config_root)
        ruleset = provider.get_rules(client_id)

        assert isinstance(ruleset, RuleSet)
        assert isinstance(provider, RuleProvider)
        assert ruleset.client_id == "acme"
        assert len(ruleset.rules) == 1
        assert ruleset.rules[0].id == "max-valor"
        assert ruleset.rules[0].effect == RuleEffect.DENY
        assert ruleset.rules[0].message == "valor acima do limite"

    def test_file_absent_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        provider = FileSystemRuleProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_rules("nonexistent")

        assert exc_info.value.client_id == "nonexistent"
        assert "nonexistent" in str(exc_info.value.path)
        assert "rules.yaml" in str(exc_info.value.path)
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    def test_malformed_yaml_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 1\nclient_id: acme\ninvalid: [unclosed")

        provider = FileSystemRuleProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_rules(client_id)

        assert exc_info.value.client_id == "acme"

    def test_schema_version_wrong_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 99\nclient_id: acme\nrules: []")

        provider = FileSystemRuleProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_rules(client_id)

        assert exc_info.value.client_id == "acme"

    def test_extra_field_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 1\nclient_id: acme\nrules: []\nunknown_field: value")

        provider = FileSystemRuleProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_rules(client_id)

        assert exc_info.value.client_id == "acme"

    def test_client_id_mismatch_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        # Simulates copying acme's rules.yaml into beta's folder without
        # updating the client_id field inside it.
        config_dir = config_root / "beta" / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 1\nclient_id: acme\nrules: []")

        provider = FileSystemRuleProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_rules("beta")

        assert exc_info.value.client_id == "beta"
        assert "does not match" in str(exc_info.value)

    def test_no_cache_reflects_file_changes(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text(
            """\
schema_version: 1
client_id: acme
rules:
  - id: rule1
    description: First rule
    condition: { "==": [1, 1] }
    effect: deny
"""
        )

        provider = FileSystemRuleProvider(config_root)
        ruleset1 = provider.get_rules(client_id)
        assert len(ruleset1.rules) == 1
        assert ruleset1.rules[0].id == "rule1"

        rules_yaml.write_text(
            """\
schema_version: 1
client_id: acme
rules:
  - id: rule1
    description: First rule
    condition: { "==": [1, 1] }
    effect: deny
  - id: rule2
    description: Second rule
    condition: { "==": [2, 2] }
    effect: allow
"""
        )

        ruleset2 = provider.get_rules(client_id)
        assert len(ruleset2.rules) == 2
        assert ruleset2.rules[1].id == "rule2"


class TestFileSystemSkillProvider:
    def test_load_valid_skills_yaml(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            """\
schema_version: 1
client_id: acme
skills:
  - name: send_email
    description: Sends an email via SMTP
    method: POST
    url_template: https://api.example.com/email
    allowed_domains:
      - api.example.com
      - mail.*.example.com
    timeout_seconds: 60.0
"""
        )

        provider = FileSystemSkillProvider(config_root)
        skillset = provider.get_skills(client_id)

        assert isinstance(skillset, SkillSet)
        assert isinstance(provider, SkillProvider)
        assert skillset.client_id == "acme"
        assert len(skillset.skills) == 1
        assert skillset.skills[0].name == "send_email"
        assert skillset.skills[0].method == HttpMethod.POST
        assert skillset.skills[0].timeout_seconds == 60.0

    def test_file_absent_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        provider = FileSystemSkillProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_skills("nonexistent")

        assert exc_info.value.client_id == "nonexistent"
        assert "nonexistent" in str(exc_info.value.path)
        assert "skills.yaml" in str(exc_info.value.path)
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    def test_malformed_yaml_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text("schema_version: 1\nclient_id: acme\ninvalid: [unclosed")

        provider = FileSystemSkillProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_skills(client_id)

        assert exc_info.value.client_id == "acme"

    def test_schema_version_wrong_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text("schema_version: 99\nclient_id: acme\nskills: []")

        provider = FileSystemSkillProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_skills(client_id)

        assert exc_info.value.client_id == "acme"

    def test_extra_field_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            "schema_version: 1\nclient_id: acme\nskills: []\nunknown_field: value"
        )

        provider = FileSystemSkillProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_skills(client_id)

        assert exc_info.value.client_id == "acme"

    def test_client_id_mismatch_raises_config_load_error(self, tmp_path: Path) -> None:
        config_root = tmp_path
        # Simulates copying acme's skills.yaml into beta's folder without
        # updating the client_id field inside it.
        config_dir = config_root / "beta" / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text("schema_version: 1\nclient_id: acme\nskills: []")

        provider = FileSystemSkillProvider(config_root)

        with pytest.raises(ConfigLoadError) as exc_info:
            provider.get_skills("beta")

        assert exc_info.value.client_id == "beta"
        assert "does not match" in str(exc_info.value)

    def test_no_cache_reflects_file_changes(self, tmp_path: Path) -> None:
        config_root = tmp_path
        client_id = "acme"
        config_dir = config_root / client_id / "config"
        config_dir.mkdir(parents=True)

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            """\
schema_version: 1
client_id: acme
skills:
  - name: skill1
    description: First skill
    method: GET
    url_template: https://api.example.com/1
    allowed_domains:
      - api.example.com
"""
        )

        provider = FileSystemSkillProvider(config_root)
        skillset1 = provider.get_skills(client_id)
        assert len(skillset1.skills) == 1
        assert skillset1.skills[0].name == "skill1"

        skills_yaml.write_text(
            """\
schema_version: 1
client_id: acme
skills:
  - name: skill1
    description: First skill
    method: GET
    url_template: https://api.example.com/1
    allowed_domains:
      - api.example.com
  - name: skill2
    description: Second skill
    method: POST
    url_template: https://api.example.com/2
    allowed_domains:
      - api.example.com
"""
        )

        skillset2 = provider.get_skills(client_id)
        assert len(skillset2.skills) == 2
        assert skillset2.skills[1].name == "skill2"


CLIENTS_ROOT = Path(__file__).resolve().parent.parent / "clients"


def test_example_rules_fixture_is_valid():
    ruleset = FileSystemRuleProvider(CLIENTS_ROOT).get_rules("example")

    assert ruleset.schema_version == 1
    assert ruleset.client_id == "example"
    assert len(ruleset.rules) > 0


def test_example_skills_fixture_is_valid():
    skillset = FileSystemSkillProvider(CLIENTS_ROOT).get_skills("example")

    assert skillset.schema_version == 1
    assert skillset.client_id == "example"
    assert len(skillset.skills) > 0
