"""Test helper for discovering available MCP models.

This module provides utilities for making tests model-agnostic
by discovering and using whatever models are available.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from odoo_mcp.access_control import AccessController
from odoo_mcp.config import OdooConfig

logger = logging.getLogger(__name__)


@dataclass
class TestModelInfo:
    """Information about a model available for testing."""

    model: str
    can_read: bool
    can_write: bool
    can_create: bool
    can_unlink: bool

    @property
    def readable(self) -> bool:
        """Check if model is readable."""
        return self.can_read

    @property
    def writable(self) -> bool:
        """Check if model is writable."""
        return self.can_write

    @property
    def fully_accessible(self) -> bool:
        """Check if model has all permissions."""
        return all([self.can_read, self.can_write, self.can_create, self.can_unlink])


class ModelDiscovery:
    """Discovers available models for testing.

    This class helps tests adapt to whatever models are
    currently enabled in the MCP configuration.
    """

    def __init__(self, config: OdooConfig):
        """Initialize model discovery.

        Args:
            config: Odoo configuration
        """
        self.config = config
        self.controller = AccessController(config)
        self._cache: Optional[Dict[str, TestModelInfo]] = None

    def discover_models(self) -> Dict[str, TestModelInfo]:
        """Discover all available models and their permissions.

        Returns:
            Dictionary mapping model names to their test info
        """
        if self._cache is not None:
            return self._cache

        models = {}
        try:
            # Get all enabled models
            enabled = self.controller.get_enabled_models()

            for model_info in enabled:
                model_name = model_info.get("model")
                if not model_name:
                    continue

                # Get permissions for this model
                perms = self.controller.get_model_permissions(model_name)

                models[model_name] = TestModelInfo(
                    model=model_name,
                    can_read=perms.can_read,
                    can_write=perms.can_write,
                    can_create=perms.can_create,
                    can_unlink=perms.can_unlink,
                )

        except Exception as e:
            logger.warning(f"Failed to discover models: {e}")

        self._cache = models
        return models

    def get_readable_model(self) -> Optional[TestModelInfo]:
        """Get any model with read permission.

        Returns:
            TestModelInfo or None if no readable models
        """
        models = self.discover_models()

        for model in models.values():
            if model.readable:
                return model

        return None

    def get_writable_model(self) -> Optional[TestModelInfo]:
        """Get any model with write permission.

        Returns:
            TestModelInfo or None if no writable models
        """
        models = self.discover_models()

        for model in models.values():
            if model.writable:
                return model

        return None

    def get_fully_accessible_model(self) -> Optional[TestModelInfo]:
        """Get any model with all permissions.

        Returns:
            TestModelInfo or None if no fully accessible models
        """
        models = self.discover_models()

        for model in models.values():
            if model.fully_accessible:
                return model

        return None

    def get_common_models(self) -> List[TestModelInfo]:
        """Get commonly available models for testing.

        Prioritizes models like res.partner, res.users, etc.

        Returns:
            List of available common models
        """
        models = self.discover_models()
        common_model_names = [
            "res.partner",
            "res.users",
            "res.company",
            "product.product",
            "sale.order",
            "purchase.order",
        ]

        available = []
        for name in common_model_names:
            if name in models:
                available.append(models[name])

        return available

    def get_disabled_model(self) -> str:
        """Get a model name that is NOT enabled.

        Returns:
            Name of a model that should not be accessible
        """
        models = self.discover_models()

        # These system models are rarely enabled in MCP
        system_models = [
            "ir.model",
            "ir.model.fields",
            "ir.ui.view",
            "ir.rule",
            "base.automation",
            "ir.config_parameter",
            "ir.cron",
            "ir.module.module",
        ]

        for model in system_models:
            if model not in models:
                return model

        # If all system models are somehow enabled,
        # return a non-existent model
        return "non.existent.model"

    def require_readable_model(self) -> TestModelInfo:
        """Get a readable model or skip test.

        Returns:
            TestModelInfo for a readable model

        Raises:
            pytest.skip if no readable models available
        """
        import pytest

        model = self.get_readable_model()
        if not model:
            pytest.skip("No readable models available for testing")

        return model

    def require_writable_model(self) -> TestModelInfo:
        """Get a writable model or skip test.

        Returns:
            TestModelInfo for a writable model

        Raises:
            pytest.skip if no writable models available
        """
        import pytest

        model = self.get_writable_model()
        if not model:
            pytest.skip("No writable models available for testing")

        return model
