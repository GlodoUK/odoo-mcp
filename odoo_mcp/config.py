"""Configuration management for Odoo MCP Server.

This module handles loading and validation of environment variables
for connecting to Odoo via XML-RPC.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv


@dataclass
class OdooConfig:
    """Configuration for Odoo connection and MCP server settings."""

    # Odoo URL (required)
    url: str = ""

    # Authentication — API key only.
    # JSON/2:   plain API key.
    # XML-RPC:  'username:api_key' (split on first colon).
    api_key: Optional[str] = None

    # Optional fields with defaults
    database: Optional[str] = None
    log_level: str = "INFO"
    default_limit: int = 10
    max_limit: int = 100
    max_smart_fields: int = 15

    # MCP transport configuration
    transport: Literal["stdio", "streamable-http"] = "stdio"
    host: str = "localhost"
    port: int = 8000

    # Read-only mode: if True, write tools are not registered
    readonly: bool = True

    # API version: "xmlrpc" (Odoo 14-19) or "json2" (Odoo 19+ only)
    api_version: Literal["xmlrpc", "json2"] = "xmlrpc"

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate URL
        if not self.url:
            raise ValueError("ODOO_URL is required")

        # Ensure URL format
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("ODOO_URL must start with http:// or https://")

        # Validate authentication — API keys only; HTTP transport defers to request-time Bearer
        is_http = self.transport == "streamable-http"
        has_api_key = bool(self.api_key)

        if not is_http and not has_api_key:
            raise ValueError(
                "ODOO_API_KEY is required for stdio transport. "
                "For JSON/2 use a plain API key; "
                "for XML-RPC use 'username:api_key' format."
            )

        # Validate numeric fields
        if self.default_limit <= 0:
            raise ValueError("ODOO_MCP_DEFAULT_LIMIT must be positive")

        if self.max_limit <= 0:
            raise ValueError("ODOO_MCP_MAX_LIMIT must be positive")

        if self.default_limit > self.max_limit:
            raise ValueError("ODOO_MCP_DEFAULT_LIMIT cannot exceed ODOO_MCP_MAX_LIMIT")

        # Validate log level
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(
                f"Invalid log level: {self.log_level}. "
                f"Must be one of: {', '.join(valid_log_levels)}"
            )

        # Validate transport
        valid_transports = {"stdio", "streamable-http"}
        if self.transport not in valid_transports:
            raise ValueError(
                f"Invalid transport: {self.transport}. "
                f"Must be one of: {', '.join(valid_transports)}"
            )

        # Validate port
        if self.port <= 0 or self.port > 65535:
            raise ValueError("Port must be between 1 and 65535")

        # Validate API version
        valid_api_versions = {"xmlrpc", "json2"}
        if self.api_version not in valid_api_versions:
            raise ValueError(
                f"Invalid API version: {self.api_version}. "
                f"Must be one of: {', '.join(valid_api_versions)}"
            )

    @property
    def uses_api_key(self) -> bool:
        """Check if configuration has a static API key (stdio transport)."""
        return bool(self.api_key)

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "OdooConfig":
        """Create configuration from environment variables.

        Args:
            env_file: Optional path to .env file

        Returns:
            OdooConfig: Validated configuration object
        """
        return load_config(env_file)


def load_config(env_file: Optional[Path] = None) -> OdooConfig:
    """Load configuration from environment variables and .env file.

    Args:
        env_file: Optional path to .env file. If not provided,
                 looks for .env in current directory.

    Returns:
        OdooConfig: Validated configuration object

    Raises:
        ValueError: If required configuration is missing or invalid
    """
    # Check if we have a .env file or environment variables
    if env_file:
        if not env_file.exists():
            raise ValueError(
                f"Configuration file not found: {env_file}\n"
                "Please create a .env file based on .env.example"
            )
        load_dotenv(env_file)
    else:
        # Try to load .env from current directory
        default_env = Path(".env")
        env_loaded = False

        if default_env.exists():
            load_dotenv(default_env)
            env_loaded = True

        # If no .env file found and no ODOO_URL in environment, raise error
        if not env_loaded and not os.getenv("ODOO_URL"):
            raise ValueError(
                "No .env file found and ODOO_URL not set in environment.\n"
                "Please create a .env file based on .env.example or set environment variables."
            )

    # Helper function to get int with default
    def get_int_env(key: str, default: int) -> int:
        value = os.getenv(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{key} must be a valid integer") from None

    # Create configuration
    config = OdooConfig(
        url=os.getenv("ODOO_URL", "").strip(),
        api_key=os.getenv("ODOO_API_KEY", "").strip() or None,
        database=os.getenv("ODOO_DB", "").strip() or None,
        log_level=os.getenv("ODOO_MCP_LOG_LEVEL", "INFO").strip(),
        default_limit=get_int_env("ODOO_MCP_DEFAULT_LIMIT", 10),
        max_limit=get_int_env("ODOO_MCP_MAX_LIMIT", 100),
        max_smart_fields=get_int_env("ODOO_MCP_MAX_SMART_FIELDS", 15),
        transport=os.getenv("ODOO_MCP_TRANSPORT", "stdio").strip(),
        host=os.getenv("ODOO_MCP_HOST", "localhost").strip(),
        port=get_int_env("ODOO_MCP_PORT", 8000),
        readonly=os.getenv("ODOO_READONLY", "true").strip().lower() != "false",
        api_version=os.getenv("ODOO_API_VERSION", "xmlrpc").strip().lower(),
    )

    return config


# Singleton configuration instance
_config: Optional[OdooConfig] = None


def get_config() -> OdooConfig:
    """Get the singleton configuration instance.

    Returns:
        OdooConfig: The configuration object

    Raises:
        ValueError: If configuration is not yet loaded
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: OdooConfig) -> None:
    """Set the singleton configuration instance.

    This is primarily useful for testing.

    Args:
        config: The configuration object to set
    """
    global _config
    _config = config


def reset_config() -> None:
    """Reset the singleton configuration instance.

    This is primarily useful for testing.
    """
    global _config
    _config = None
