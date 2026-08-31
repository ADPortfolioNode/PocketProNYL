# File Location: /app/config/manager.py
from typing import Dict, Any

class ConfigManager:
    def __init__(self):
        self._config: Dict[str, Any] = {}

    def get_config(self) -> Dict[str, Any]:
        return self._config

    def set_config(self, new_config: Dict[str, Any]) -> None:
        self._config.update(new_config)

config_manager = ConfigManager()

def get_config() -> ConfigManager:
    return config_manager