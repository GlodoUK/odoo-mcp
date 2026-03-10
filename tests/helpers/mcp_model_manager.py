"""Test helper for managing MCP enabled models during tests.

This module provides utilities for enabling/disabling models in MCP
for testing purposes, making tests model-agnostic.
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from odoo_mcp.config import OdooConfig
from odoo_mcp.odoo_connection import OdooConnection

logger = logging.getLogger(__name__)


@dataclass
class ModelPermissions:
    """Permissions for a model in MCP."""

    model: str
    allow_read: bool = True
    allow_create: bool = False
    allow_write: bool = False
    allow_unlink: bool = False


class MCPModelManager:
    """Manages MCP enabled models for testing.

    This class provides methods to:
    - Save current MCP model configuration
    - Enable/disable models for tests
    - Restore original configuration after tests
    """

    def __init__(self, config: OdooConfig):
        """Initialize the model manager.

        Args:
            config: Odoo configuration with admin credentials
        """
        self.config = config
        self.connection = OdooConnection(config)
        self._original_state: Dict[str, Dict] = {}
        self._uid: Optional[int] = None

    def __enter__(self):
        """Context manager entry."""
        self.connection.connect()
        self.connection.authenticate()
        self._uid = self.connection.uid
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.connection.disconnect()
        return False

    def save_state(self) -> None:
        """Save the current state of MCP enabled models."""
        # Search for all enabled models
        enabled_records = self.connection.execute_kw(
            "mcp.enabled.model",
            "search_read",
            [[]],
            {
                "fields": [
                    "model_name",
                    "active",
                    "allow_read",
                    "allow_create",
                    "allow_write",
                    "allow_unlink",
                ]
            },
        )

        # Store the current state
        self._original_state = {
            record["model_name"]: {
                "id": record["id"],
                "active": record["active"],
                "allow_read": record["allow_read"],
                "allow_create": record["allow_create"],
                "allow_write": record["allow_write"],
                "allow_unlink": record["allow_unlink"],
            }
            for record in enabled_records
        }

        logger.info(f"Saved MCP state for {len(self._original_state)} models")

    def restore_state(self) -> None:
        """Restore the original state of MCP enabled models."""
        if not self._original_state:
            logger.warning("No original state to restore")
            return

        # Get current enabled models
        current_records = self.connection.execute_kw(
            "mcp.enabled.model", "search_read", [[]], {"fields": ["model_name"]}
        )
        current_models = {r["model_name"]: r["id"] for r in current_records}

        # Delete models that weren't originally enabled
        for model_name, record_id in current_models.items():
            if model_name not in self._original_state:
                self.connection.execute_kw("mcp.enabled.model", "unlink", [[record_id]])
                logger.info(f"Removed test model: {model_name}")

        # Restore original models
        for model_name, state in self._original_state.items():
            if model_name in current_models:
                # Update existing record
                self.connection.execute_kw(
                    "mcp.enabled.model",
                    "write",
                    [
                        [state["id"]],
                        {
                            "active": state["active"],
                            "allow_read": state["allow_read"],
                            "allow_create": state["allow_create"],
                            "allow_write": state["allow_write"],
                            "allow_unlink": state["allow_unlink"],
                        },
                    ],
                )
            else:
                # Recreate deleted record
                model_id = self._get_model_id(model_name)
                if model_id:
                    self.connection.execute_kw(
                        "mcp.enabled.model",
                        "create",
                        [
                            {
                                "model_id": model_id,
                                "active": state["active"],
                                "allow_read": state["allow_read"],
                                "allow_create": state["allow_create"],
                                "allow_write": state["allow_write"],
                                "allow_unlink": state["allow_unlink"],
                            }
                        ],
                    )

        logger.info("Restored original MCP state")

    def enable_models(self, models: List[ModelPermissions]) -> None:
        """Enable specific models for MCP access.

        Args:
            models: List of models to enable with their permissions
        """
        for model_perm in models:
            self._ensure_model_enabled(model_perm)

    def disable_all_models(self) -> None:
        """Disable all models for MCP access."""
        # Get all enabled models
        enabled_ids = self.connection.execute_kw(
            "mcp.enabled.model", "search", [[("active", "=", True)]]
        )

        if enabled_ids:
            # Set all to inactive
            self.connection.execute_kw(
                "mcp.enabled.model", "write", [enabled_ids, {"active": False}]
            )
            logger.info(f"Disabled {len(enabled_ids)} models")

    def _ensure_model_enabled(self, model_perm: ModelPermissions) -> None:
        """Ensure a model is enabled with specific permissions.

        Args:
            model_perm: Model and its permissions
        """
        # Check if model already exists
        existing_ids = self.connection.execute_kw(
            "mcp.enabled.model", "search", [[("model_name", "=", model_perm.model)]]
        )

        if existing_ids:
            # Update existing record
            self.connection.execute_kw(
                "mcp.enabled.model",
                "write",
                [
                    existing_ids,
                    {
                        "active": True,
                        "allow_read": model_perm.allow_read,
                        "allow_create": model_perm.allow_create,
                        "allow_write": model_perm.allow_write,
                        "allow_unlink": model_perm.allow_unlink,
                    },
                ],
            )
            logger.info(f"Updated model: {model_perm.model}")
        else:
            # Create new record
            model_id = self._get_model_id(model_perm.model)
            if model_id:
                self.connection.execute_kw(
                    "mcp.enabled.model",
                    "create",
                    [
                        {
                            "model_id": model_id,
                            "active": True,
                            "allow_read": model_perm.allow_read,
                            "allow_create": model_perm.allow_create,
                            "allow_write": model_perm.allow_write,
                            "allow_unlink": model_perm.allow_unlink,
                        }
                    ],
                )
                logger.info(f"Enabled model: {model_perm.model}")
            else:
                logger.warning(f"Model not found in Odoo: {model_perm.model}")

    def _get_model_id(self, model_name: str) -> Optional[int]:
        """Get the ir.model ID for a model name.

        Args:
            model_name: Technical name of the model

        Returns:
            Model ID or None if not found
        """
        model_ids = self.connection.execute_kw(
            "ir.model", "search", [[("model", "=", model_name)]], {"limit": 1}
        )

        return model_ids[0] if model_ids else None

    def get_enabled_models(self) -> List[str]:
        """Get list of currently enabled models.

        Returns:
            List of model names that are enabled
        """
        records = self.connection.execute_kw(
            "mcp.enabled.model",
            "search_read",
            [[("active", "=", True)]],
            {"fields": ["model_name"]},
        )

        return [r["model_name"] for r in records]

    def get_first_enabled_model(self) -> Optional[Tuple[str, Dict[str, bool]]]:
        """Get the first enabled model with its permissions.

        Returns:
            Tuple of (model_name, permissions) or None if no models enabled
        """
        records = self.connection.execute_kw(
            "mcp.enabled.model",
            "search_read",
            [[("active", "=", True), ("allow_read", "=", True)]],
            {
                "fields": [
                    "model_name",
                    "allow_read",
                    "allow_create",
                    "allow_write",
                    "allow_unlink",
                ],
                "limit": 1,
            },
        )

        if records:
            record = records[0]
            return (
                record["model_name"],
                {
                    "read": record["allow_read"],
                    "create": record["allow_create"],
                    "write": record["allow_write"],
                    "unlink": record["allow_unlink"],
                },
            )
        return None


def create_admin_config() -> OdooConfig:
    """Create admin configuration for managing MCP models.

    Returns:
        OdooConfig with admin credentials
    """
    # Use username/password auth for admin operations
    return OdooConfig(
        url=os.getenv("ODOO_URL", "http://localhost:8069"),
        username=os.getenv("ODOO_USER", "admin"),
        password=os.getenv("ODOO_PASSWORD", "admin"),
        database=os.getenv("ODOO_DB"),
    )
