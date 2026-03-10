"""Tests for authentication functionality in OdooConnection.

This module tests the simplified API-key-only authentication flow.
XML-RPC uses 'username:api_key' format parsed from ODOO_API_KEY.
"""

import os
from unittest.mock import Mock
from xmlrpc.client import Fault

import pytest

from odoo_mcp.config import OdooConfig
from odoo_mcp.odoo_connection import OdooConnection, OdooConnectionError

from .conftest import ODOO_SERVER_AVAILABLE


class TestAuthentication:
    """Test authentication functionality."""

    @pytest.fixture
    def config_xmlrpc(self):
        """Create configuration with XML-RPC style api_key (username:api_key)."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="admin:test_api_key",
            database=os.getenv("ODOO_DB", "testdb"),
        )

    @pytest.fixture
    def connection(self, config_xmlrpc):
        """Create connection with XML-RPC config."""
        conn = OdooConnection(config_xmlrpc)
        conn._connected = True
        return conn

    def test_authenticate_not_connected(self, config_xmlrpc):
        """Test authenticate raises error when not connected."""
        conn = OdooConnection(config_xmlrpc)
        with pytest.raises(OdooConnectionError, match="Not connected"):
            conn.authenticate()

    def test_authenticate_success(self, connection):
        """Test successful authentication with username:api_key format."""
        mock_common = Mock()
        mock_common.authenticate.return_value = 2
        connection._common_proxy = mock_common

        connection.authenticate("testdb")

        assert connection.is_authenticated
        assert connection.uid == 2
        assert connection.database == "testdb"
        assert connection._api_key == "test_api_key"

        # Verify authenticate was called with parsed username and api_key
        mock_common.authenticate.assert_called_once_with("testdb", "admin", "test_api_key", {})

    def test_authenticate_invalid_key(self, connection):
        """Test authentication with invalid API key."""
        mock_common = Mock()
        mock_common.authenticate.return_value = False
        connection._common_proxy = mock_common

        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            connection.authenticate("testdb")

        assert not connection.is_authenticated

    def test_authenticate_xmlrpc_fault(self, connection):
        """Test authentication with XML-RPC fault."""
        mock_common = Mock()
        mock_common.authenticate.side_effect = Fault(1, "Access Denied")
        connection._common_proxy = mock_common

        with pytest.raises(OdooConnectionError, match="Authentication failed"):
            connection.authenticate("testdb")

        assert not connection.is_authenticated

    def test_authenticate_invalid_format_raises(self):
        """Test that missing colon in api_key raises error."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="plainkey_no_colon",
            database="testdb",
        )
        conn = OdooConnection(config)
        conn._connected = True
        mock_common = Mock()
        conn._common_proxy = mock_common

        with pytest.raises(OdooConnectionError, match="Invalid ODOO_API_KEY format"):
            conn.authenticate("testdb")

    def test_authenticate_uses_config_database(self, config_xmlrpc):
        """Test that config database is used when not specified."""
        conn = OdooConnection(config_xmlrpc)
        conn._connected = True
        mock_common = Mock()
        mock_common.authenticate.return_value = 5
        conn._common_proxy = mock_common

        conn.authenticate()

        assert conn.database == config_xmlrpc.database

    def test_authentication_state_cleared_on_disconnect(self, connection):
        """Test authentication state is cleared on disconnect."""
        # Set authentication state
        connection._authenticated = True
        connection._uid = 2
        connection._database = "testdb"
        connection._api_key = "test_api_key"

        # Disconnect
        connection.disconnect()

        # Verify state cleared
        assert not connection.is_authenticated
        assert connection.uid is None
        assert connection.database is None
        assert connection._api_key is None

    def test_execute_kw_uses_parsed_api_key(self, connection):
        """Test that execute_kw uses the parsed api_key (not full username:api_key)."""
        connection._authenticated = True
        connection._uid = 2
        connection._database = "testdb"
        connection._api_key = "test_api_key"

        mock_proxy = Mock()
        mock_proxy.execute_kw.return_value = []
        connection._object_proxy = mock_proxy

        connection.search("res.partner", [])

        # Should pass the parsed api_key, not the full "admin:test_api_key"
        mock_proxy.execute_kw.assert_called_once_with(
            "testdb", 2, "test_api_key", "res.partner", "search", [[]], {}
        )


class TestEndpointConfiguration:
    """Test that OdooConnection uses standard XML-RPC endpoints."""

    def test_standard_endpoints(self):
        """Test that OdooConnection uses standard Odoo XML-RPC endpoints."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="admin:test_api_key",
            database=os.getenv("ODOO_DB", "testdb"),
        )
        conn = OdooConnection(config)

        assert conn.DB_ENDPOINT == "/xmlrpc/db"
        assert conn.COMMON_ENDPOINT == "/xmlrpc/2/common"
        assert conn.OBJECT_ENDPOINT == "/xmlrpc/2/object"


@pytest.mark.skipif(not ODOO_SERVER_AVAILABLE, reason="Odoo server not available")
@pytest.mark.xmlrpc_only
class TestAuthenticationIntegration:
    """Integration tests with real Odoo server."""

    @pytest.fixture
    def real_config(self):
        """Create configuration with real API key (username:api_key format)."""
        return OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key=os.getenv("ODOO_API_KEY"),
            database=None,  # Let it auto-select
        )

    def test_real_api_key_authentication(self, real_config):
        """Test API key authentication with real server."""
        with OdooConnection(real_config) as conn:
            conn.authenticate()

            assert conn.is_authenticated
            assert conn.uid is not None
            assert conn.database is not None

            print(f"Authenticated: uid={conn.uid}, db={conn.database}")

    def test_real_invalid_api_key(self):
        """Test authentication with invalid API key."""
        config = OdooConfig(
            url=os.getenv("ODOO_URL", "http://localhost:8069"),
            api_key="admin:invalid_key_12345",
            database=os.getenv("ODOO_DB"),
        )

        with OdooConnection(config) as conn:
            with pytest.raises(OdooConnectionError, match="Authentication failed"):
                conn.authenticate()


if __name__ == "__main__":
    # Run integration tests when executed directly
    pytest.main([__file__, "-v", "-k", "Integration"])
