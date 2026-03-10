"""Tests for basic MCP resource handling."""

from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from odoo_mcp.config import OdooConfig
from odoo_mcp.error_handling import (
    NotFoundError,
    ValidationError,
)
from odoo_mcp.odoo_connection import OdooConnection, OdooConnectionError
from odoo_mcp.resources import OdooResourceHandler, register_resources


def _create_connection(config):
    """Create the appropriate connection based on api_version."""
    if config.api_version == "json2":
        from odoo_mcp.odoo_json2_connection import OdooJSON2Connection

        return OdooJSON2Connection(config)
    return OdooConnection(config)


@pytest.fixture
def test_config():
    """Create test configuration."""
    # Load real config from environment for integration tests
    from odoo_mcp.config import get_config

    return get_config()


@pytest.fixture
def mock_config():
    """Create mock configuration."""
    config = Mock(spec=OdooConfig)
    config.default_limit = 10
    config.max_limit = 100
    return config


@pytest.fixture
def mock_connection():
    """Create mock OdooConnection."""
    conn = Mock(spec=OdooConnection)
    conn.is_authenticated = True
    conn.search = Mock()
    conn.read = Mock()
    conn.fields_get = Mock(return_value={})  # Mock fields_get for formatter
    return conn


@pytest.fixture
def mock_app():
    """Create mock FastMCP app."""
    app = Mock(spec=FastMCP)
    app.resource = Mock()
    # Make resource decorator return the function as-is
    app.resource.return_value = lambda func: func
    return app


@pytest.fixture
def resource_handler(mock_app, mock_connection, mock_config):
    """Create OdooResourceHandler instance."""
    return OdooResourceHandler(mock_app, mock_connection, mock_config)


class TestOdooResourceHandler:
    """Test OdooResourceHandler functionality."""

    def test_init(self, mock_app, mock_connection, mock_config):
        """Test handler initialization."""
        handler = OdooResourceHandler(mock_app, mock_connection, mock_config)

        assert handler.app == mock_app
        assert handler.connection == mock_connection
        assert handler.config == mock_config

        # Check that resources were registered
        assert mock_app.resource.call_count >= 1

    @pytest.mark.asyncio
    async def test_handle_record_retrieval_success(self, resource_handler, mock_connection):
        """Test successful record retrieval."""
        # Setup mocks
        mock_connection.search.return_value = [1]
        mock_connection.read.return_value = [
            {
                "id": 1,
                "name": "Test Partner",
                "display_name": "Test Partner",
                "email": "test@example.com",
                "is_company": True,
                "country_id": (1, "United States"),
                "child_ids": [2, 3, 4],
                "phone": False,
                "__last_update": "2025-01-01 00:00:00",
            }
        ]

        # Test retrieval
        result = await resource_handler._handle_record_retrieval("res.partner", "1")

        # Verify calls
        mock_connection.search.assert_called_once_with("res.partner", [("id", "=", 1)])
        mock_connection.read.assert_called_once_with("res.partner", [1])

        # Check result format (using new formatter)
        assert "Record: res.partner/1" in result
        assert "Name: Test Partner" in result
        assert "=" * 50 in result  # Separator line
        assert "Fields:" in result or "Relationships:" in result

    @pytest.mark.asyncio
    async def test_handle_record_retrieval_not_found(self, resource_handler, mock_connection):
        """Test record not found error."""
        # Setup mocks
        mock_connection.search.return_value = []

        # Test retrieval
        with pytest.raises(NotFoundError) as exc_info:
            await resource_handler._handle_record_retrieval("res.partner", "999")

        assert "Record not found: res.partner with ID 999 does not exist" in str(exc_info.value)

        # Verify calls
        mock_connection.search.assert_called_once_with("res.partner", [("id", "=", 999)])
        mock_connection.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_record_retrieval_invalid_id(self, resource_handler):
        """Test invalid record ID."""
        # Test with non-numeric ID
        with pytest.raises(ValidationError) as exc_info:
            await resource_handler._handle_record_retrieval("res.partner", "abc")

        assert "Invalid record ID 'abc'" in str(exc_info.value)

        # Test with negative ID
        with pytest.raises(ValidationError) as exc_info:
            await resource_handler._handle_record_retrieval("res.partner", "-5")

        assert "Record ID must be positive" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_record_retrieval_connection_error(
        self, resource_handler, mock_connection
    ):
        """Test connection error during retrieval."""
        # Setup mock to raise connection error
        mock_connection.search.side_effect = OdooConnectionError("Connection lost")

        # Test retrieval
        with pytest.raises(ValidationError) as exc_info:
            await resource_handler._handle_record_retrieval("res.partner", "1")

        assert "Connection error:" in str(exc_info.value)


class TestRegisterResources:
    """Test register_resources function."""

    def test_register_resources(self, mock_app, mock_connection, mock_config):
        """Test resource registration."""
        handler = register_resources(mock_app, mock_connection, mock_config)

        assert isinstance(handler, OdooResourceHandler)
        assert handler.app == mock_app
        assert handler.connection == mock_connection
        assert handler.config == mock_config

        # Check that resources were registered
        assert mock_app.resource.call_count >= 1


class TestResourceIntegration:
    """Integration tests with real Odoo server."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_real_record_retrieval(self, test_config):
        """Test record retrieval with real server."""
        # Create real connection
        connection = _create_connection(test_config)
        connection.connect()
        connection.authenticate()

        # Create FastMCP app
        app = FastMCP("test-app")

        # Register resources
        handler = register_resources(app, connection, test_config)

        try:
            # Search for a partner record
            partner_ids = connection.search("res.partner", [], limit=1)

            if partner_ids:
                # Test retrieval
                result = await handler._handle_record_retrieval("res.partner", str(partner_ids[0]))

                # Verify result format
                assert f"Record: res.partner/{partner_ids[0]}" in result
                assert "Name:" in result
                assert "=" * 50 in result  # Separator line

        finally:
            connection.disconnect()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_real_record_not_found(self, test_config):
        """Test record not found with real server."""
        # Create real connection
        connection = _create_connection(test_config)
        connection.connect()
        connection.authenticate()

        # Create FastMCP app
        app = FastMCP("test-app")

        # Register resources
        handler = register_resources(app, connection, test_config)

        try:
            # Test with non-existent ID
            with pytest.raises(NotFoundError):
                await handler._handle_record_retrieval("res.partner", "999999999")

        finally:
            connection.disconnect()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_real_nonexistent_model(self, test_config):
        """Test error with nonexistent model with real server."""
        # Create real connection
        connection = _create_connection(test_config)
        connection.connect()
        connection.authenticate()

        # Create FastMCP app
        app = FastMCP("test-app")

        # Register resources
        handler = register_resources(app, connection, test_config)

        try:
            # Use a fake model that causes a connection error
            with pytest.raises(ValidationError):
                await handler._handle_record_retrieval("nonexistent.model.xyz", "1")

        finally:
            connection.disconnect()
