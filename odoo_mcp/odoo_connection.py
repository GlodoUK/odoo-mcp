"""Odoo XML-RPC connection management.

This module provides the OdooConnection class for managing connections
to Odoo via standard XML-RPC endpoints (/xmlrpc/2/).
"""

import logging
import socket
import xmlrpc.client
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from cachetools import TTLCache

from .bearer import current_bearer_token
from .config import OdooConfig
from .error_sanitizer import ErrorSanitizer
from .exceptions import OdooConnectionError  # noqa: F401
from .performance import PerformanceManager

logger = logging.getLogger(__name__)


class OdooConnection:
    """Manages XML-RPC connections to Odoo.

    This class provides connection management, health checking, and
    proper resource cleanup for Odoo XML-RPC connections.
    """

    # Connection timeout in seconds
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        config: OdooConfig,
        timeout: int = DEFAULT_TIMEOUT,
        performance_manager: Optional[PerformanceManager] = None,
    ):
        """Initialize connection with configuration.

        Args:
            config: OdooConfig object with connection parameters
            timeout: Connection timeout in seconds
            performance_manager: Optional performance manager for optimizations
        """
        self.config = config
        self.timeout = timeout
        self._url_components = self._parse_url(config.url)

        # Standard XML-RPC endpoints
        self.DB_ENDPOINT = "/xmlrpc/db"
        self.COMMON_ENDPOINT = "/xmlrpc/2/common"
        self.OBJECT_ENDPOINT = "/xmlrpc/2/object"

        # Performance manager for optimizations
        self._performance_manager = performance_manager or PerformanceManager(config)

        # XML-RPC proxies (created on connect)
        self._db_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._common_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._object_proxy: Optional[xmlrpc.client.ServerProxy] = None

        # Connection state
        self._connected = False
        self._uid: Optional[int] = None
        self._database: Optional[str] = None
        self._api_key: Optional[str] = None  # parsed api_key portion for stdio mode
        self._authenticated = False

        # Per-token UID cache for HTTP bearer passthrough (LRU, 5-minute TTL)
        self._token_uid_cache: TTLCache = TTLCache(maxsize=256, ttl=300)

        logger.info(f"Initialized OdooConnection for {self._url_components['host']}")

    def _parse_url(self, url: str) -> Dict[str, Any]:
        """Parse and validate Odoo URL.

        Args:
            url: The Odoo server URL

        Returns:
            Dictionary with URL components

        Raises:
            OdooConnectionError: If URL is invalid
        """
        try:
            parsed = urlparse(url)

            if parsed.scheme not in ("http", "https"):
                raise OdooConnectionError(
                    f"Invalid URL scheme: {parsed.scheme}. Must be http or https"
                )

            if not parsed.hostname:
                raise OdooConnectionError("Invalid URL: missing hostname")

            port = parsed.port
            if not port:
                port = 443 if parsed.scheme == "https" else 80

            return {
                "scheme": parsed.scheme,
                "host": parsed.hostname,
                "port": port,
                "path": parsed.path.rstrip("/") or "",
                "base_url": url.rstrip("/"),
            }

        except Exception as e:
            raise OdooConnectionError(f"Failed to parse URL: {e}") from e

    def _create_transport(self) -> xmlrpc.client.Transport:
        """Create XML-RPC transport with timeout support.

        Returns:
            Configured Transport object
        """

        class TimeoutTransport(xmlrpc.client.Transport):
            def __init__(self, timeout, *args, **kwargs):
                self.timeout = timeout
                super().__init__(*args, **kwargs)

            def make_connection(self, host):
                connection = super().make_connection(host)
                if hasattr(connection, "sock") and connection.sock:
                    connection.sock.settimeout(self.timeout)
                return connection

        return TimeoutTransport(self.timeout)

    def _build_endpoint_url(self, endpoint: str) -> str:
        """Build full URL for an MCP endpoint.

        Args:
            endpoint: The MCP endpoint path

        Returns:
            Full URL for the endpoint
        """
        return f"{self._url_components['base_url']}{endpoint}"

    def connect(self) -> None:
        """Establish connection to Odoo server.

        Creates XML-RPC proxies for MCP endpoints but doesn't
        authenticate yet. Uses connection pooling for better performance.

        Raises:
            OdooConnectionError: If connection fails
        """
        if self._connected:
            logger.warning("Already connected to Odoo")
            return

        try:
            # Use connection pool for proxies with dynamic endpoints
            self._db_proxy = self._performance_manager.get_optimized_connection(self.DB_ENDPOINT)
            self._common_proxy = self._performance_manager.get_optimized_connection(
                self.COMMON_ENDPOINT
            )
            self._object_proxy = self._performance_manager.get_optimized_connection(
                self.OBJECT_ENDPOINT
            )

            # Test connection by calling server_version
            self._test_connection()

            self._connected = True
            logger.info("Successfully connected to Odoo server")

        except socket.timeout:
            raise OdooConnectionError(f"Connection timeout after {self.timeout} seconds") from None
        except socket.error as e:
            raise OdooConnectionError(
                f"Failed to connect to {self._url_components['host']}:"
                f"{self._url_components['port']}: {e}"
            ) from e
        except Exception as e:
            raise OdooConnectionError(f"Connection failed: {e}") from e

    def _test_connection(self) -> None:
        """Test connection by calling server_version.

        Raises:
            OdooConnectionError: If test fails
        """
        try:
            # Try to get server version via common endpoint
            version = self._common_proxy.version()
            logger.debug(f"Server version: {version}")
        except Exception as e:
            raise OdooConnectionError(f"Connection test failed: {e}") from e

    def disconnect(self, suppress_logging: bool = False) -> None:
        """Close connection and cleanup resources."""
        if not self._connected:
            if not suppress_logging:
                try:
                    logger.warning("Not connected to Odoo")
                except (ValueError, RuntimeError):
                    # Ignore logging errors during cleanup
                    pass
            return

        # Clear proxies (but don't close pooled connections)
        self._db_proxy = None
        self._common_proxy = None
        self._object_proxy = None

        # Clear connection state
        self._connected = False
        self._uid = None
        self._database = None
        self._api_key = None
        self._authenticated = False

        if not suppress_logging:
            try:
                logger.info("Disconnected from Odoo server")
            except (ValueError, RuntimeError):
                # Ignore logging errors during cleanup
                pass

    def check_health(self) -> Tuple[bool, str]:
        """Check connection health.

        Returns:
            Tuple of (is_healthy, status_message)
        """
        if not self._connected:
            return False, "Not connected"

        try:
            # Try to get server version as health check
            version = self._common_proxy.version()
            return True, f"Connected to Odoo {version.get('server_version', 'unknown')}"
        except socket.timeout:
            return False, f"Health check timeout after {self.timeout} seconds"
        except Exception as e:
            return False, f"Health check failed: {e}"

    def test_connection(self) -> bool:
        """Test if connection to Odoo is working.

        Returns:
            True if connection is working, False otherwise
        """
        # If not connected, try to connect first
        if not self._connected:
            try:
                self.connect()
            except Exception as e:
                logger.error(f"Failed to connect: {e}")
                return False

        # Check health
        is_healthy, _ = self.check_health()
        return is_healthy

    def close(self) -> None:
        """Close the connection (alias for disconnect)."""
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected

    @property
    def db_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get database operations proxy.

        Returns:
            XML-RPC proxy for database operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._db_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._db_proxy

    @property
    def common_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get common operations proxy.

        Returns:
            XML-RPC proxy for common operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._common_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._common_proxy

    @property
    def object_proxy(self) -> xmlrpc.client.ServerProxy:
        """Get object operations proxy.

        Returns:
            XML-RPC proxy for object operations

        Raises:
            OdooConnectionError: If not connected
        """
        if not self._connected or not self._object_proxy:
            raise OdooConnectionError("Not connected to Odoo")
        return self._object_proxy

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False

    def __del__(self):
        """Cleanup on deletion."""
        try:
            # Only disconnect if we're actually connected
            if hasattr(self, "_connected") and self._connected:
                # Suppress logging during cleanup to avoid I/O errors
                self.disconnect(suppress_logging=True)
        except (ValueError, AttributeError, RuntimeError):
            # ValueError: I/O operation on closed file
            # AttributeError: object might be partially initialized
            # RuntimeError: various cleanup-related errors
            pass

    def list_databases(self) -> List[str]:
        """List all available databases on the Odoo server.

        Returns:
            List of database names

        Raises:
            OdooConnectionError: If listing fails or not connected
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        try:
            # Call list_db method on database proxy
            databases = self.db_proxy.list()
            logger.info(f"Found {len(databases)} databases: {databases}")
            return databases  # type: ignore[invalid-return-type]  # XML-RPC proxy is untyped
        except xmlrpc.client.Fault as e:
            logger.error(f"Failed to list databases: {e}")
            raise OdooConnectionError(f"Failed to list databases: {e}") from e
        except Exception as e:
            logger.error(f"Failed to list databases: {e}")
            raise OdooConnectionError(f"Failed to list databases: {e}") from e

    def database_exists(self, db_name: str) -> bool:
        """Check if a specific database exists.

        Args:
            db_name: Name of the database to check

        Returns:
            True if database exists, False otherwise

        Raises:
            OdooConnectionError: If check fails
        """
        try:
            databases = self.list_databases()
            return db_name in databases
        except Exception as e:
            logger.error(f"Failed to check database existence: {e}")
            raise OdooConnectionError(f"Failed to check database existence: {e}") from e

    def auto_select_database(self) -> str:
        """Automatically select an appropriate database.

        Selection logic:
        1. If config.database is set, validate and use it
        2. If only one database exists, use it
        3. If multiple databases exist and one is named 'odoo', use it
        4. Otherwise raise an error

        Returns:
            Selected database name

        Raises:
            OdooConnectionError: If no suitable database can be selected
        """
        # If database is explicitly configured, use it without validation
        # Database listing may be restricted for security reasons
        if self.config.database:
            db_name = self.config.database
            logger.info(f"Using configured database: {db_name}")
            # Skip existence check as database listing might be restricted
            return db_name

        # List available databases
        try:
            databases = self.list_databases()
        except Exception as e:
            # If database listing is restricted, we cannot auto-select
            logger.warning(f"Cannot list databases (may be restricted): {e}")
            raise OdooConnectionError(
                "Database auto-selection failed. Database listing may be restricted. "
                "Please specify ODOO_DB in your configuration."
            ) from e

        # Handle different scenarios
        if not databases:
            raise OdooConnectionError("No databases found on Odoo server")

        if len(databases) == 1:
            db_name = databases[0]
            logger.info(f"Auto-selected only available database: {db_name}")
            return db_name

        # Multiple databases - check for 'odoo'
        if "odoo" in databases:
            logger.info("Auto-selected 'odoo' database from multiple options")
            return "odoo"

        # Cannot auto-select
        raise OdooConnectionError(
            f"Cannot auto-select database. Found {len(databases)} databases: "
            f"{', '.join(databases)}. Please specify ODOO_DB in configuration."
        )

    def authenticate(self, database: Optional[str] = None) -> None:
        """Authenticate with Odoo using the configured API key.

        Parses ``ODOO_API_KEY`` as ``username:api_key`` for XML-RPC and
        calls ``common.authenticate``.  Only used in stdio transport; HTTP
        transport authenticates per-request via the Bearer ContextVar.

        Args:
            database: Database name.  If not provided, uses config or
                      auto-selection via ``auto_select_database()``.

        Raises:
            OdooConnectionError: If not connected or authentication fails.
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        db_name = database or self.config.database or self.auto_select_database()

        from .bearer import parse_xmlrpc_token

        try:
            username, api_key = parse_xmlrpc_token(self.config.api_key or "")
        except ValueError as e:
            raise OdooConnectionError(
                f"Invalid ODOO_API_KEY format: {e}. XML-RPC requires 'username:api_key'."
            ) from e

        try:
            uid = self._common_proxy.authenticate(db_name, username, api_key, {})
        except xmlrpc.client.Fault as e:
            raise OdooConnectionError(f"Authentication failed: {e.faultString}") from e
        except Exception as e:
            raise OdooConnectionError(f"Authentication failed: {e}") from e

        if not uid:
            raise OdooConnectionError("Authentication failed: invalid username or API key")

        self._uid = uid  # type: ignore[invalid-assignment]
        self._database = db_name
        self._api_key = api_key
        self._authenticated = True
        logger.info(f"Authenticated as '{username}' (UID {uid}) on database '{db_name}'")

    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._authenticated

    @property
    def uid(self) -> Optional[int]:
        """Get authenticated user ID."""
        return self._uid

    @property
    def database(self) -> Optional[str]:
        """Get authenticated database name."""
        return self._database

    @property
    def performance_manager(self) -> PerformanceManager:
        """Get the performance manager instance."""
        return self._performance_manager

    def _get_uid_for_token(self, token: str, database: str) -> tuple:
        """Resolve and cache the (uid, api_key) for a Bearer token (HTTP mode).

        Parses the token as ``username:api_key``, calls XML-RPC
        ``common.authenticate``, and caches the result per token.

        Args:
            token: Bearer token in ``username:api_key`` format.
            database: Database name.

        Returns:
            ``(uid, api_key)`` tuple.

        Raises:
            OdooConnectionError: If the token format is wrong or auth fails.
        """
        cache_key = f"{database}:{token}"
        cached = self._token_uid_cache.get(cache_key)
        if cached:
            return cached

        from .bearer import parse_xmlrpc_token

        try:
            username, api_key = parse_xmlrpc_token(token)
        except ValueError as e:
            raise OdooConnectionError(str(e)) from e
        try:
            uid = self._common_proxy.authenticate(database, username, api_key, {})
        except xmlrpc.client.Fault as e:
            raise OdooConnectionError(f"Bearer token authentication failed: {e.faultString}") from e
        except Exception as e:
            raise OdooConnectionError(f"Bearer token authentication failed: {e}") from e
        if not uid:
            raise OdooConnectionError(
                "Authentication failed: invalid username or API key in Bearer token"
            )
        self._token_uid_cache[cache_key] = (uid, api_key)
        logger.debug(f"Cached UID {uid} for bearer token (database: {database})")
        return uid, api_key

    def execute(self, model: str, method: str, *args) -> Any:
        """Execute an operation on an Odoo model.

        This is a simplified interface that calls execute_kw with empty kwargs.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search', 'read')
            *args: Arguments to pass to the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        return self.execute_kw(model, method, list(args), {})

    def execute_kw(self, model: str, method: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Execute an operation on an Odoo model with keyword arguments.

        This is the main method for interacting with Odoo models via XML-RPC.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search_read')
            args: List of positional arguments for the method
            kwargs: Dictionary of keyword arguments for the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        # HTTP bearer passthrough: authenticate per-request via ContextVar
        bearer_token = current_bearer_token.get()
        if bearer_token:
            database = self._database or self.config.database
            if not database:
                raise OdooConnectionError("ODOO_DB is required for XML-RPC HTTP bearer mode")
            uid, password_or_token = self._get_uid_for_token(bearer_token, database)
        elif self._authenticated:
            database = self._database
            uid = self._uid
            password_or_token = self._api_key
        else:
            raise OdooConnectionError("Not authenticated. Call authenticate() first.")

        try:
            # Log the operation
            logger.debug(f"Executing {method} on {model} with args={args}, kwargs={kwargs}")

            # Execute via object proxy
            result = self.object_proxy.execute_kw(
                database, uid, password_or_token, model, method, args, kwargs
            )

            logger.debug("Operation completed successfully")
            return result

        except xmlrpc.client.Fault as e:
            logger.error(f"XML-RPC fault during {method} on {model}: {e}")
            # Sanitize the fault string before exposing to user
            sanitized_message = ErrorSanitizer.sanitize_xmlrpc_fault(e.faultString)
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e
        except socket.timeout:
            logger.error(f"Timeout during {method} on {model}")
            raise OdooConnectionError(f"Operation timeout after {self.timeout} seconds") from None
        except Exception as e:
            logger.error(f"Error during {method} on {model}: {e}")
            # Sanitize generic errors as well
            sanitized_message = ErrorSanitizer.sanitize_message(str(e))
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e

    def search(self, model: str, domain: List[Union[str, List[Any]]], **kwargs) -> List[int]:
        """Search for records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter (e.g., [['is_company', '=', True]])
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of record IDs matching the domain
        """
        return self.execute_kw(model, "search", [domain], kwargs)

    def read(
        self, model: str, ids: List[int], fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Read records by IDs.

        Args:
            model: The Odoo model name
            ids: List of record IDs to read
            fields: List of field names to read (None for all fields)

        Returns:
            List of dictionaries containing record data
        """
        kwargs = {}
        if fields:
            kwargs["fields"] = fields

        with self._performance_manager.monitor.track_operation(f"read_{model}"):
            records = self.execute_kw(model, "read", [ids], kwargs)

        return records

    def search_read(
        self,
        model: str,
        domain: List[Union[str, List[Any]]],
        fields: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search for records and read their data in one operation.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter
            fields: List of field names to read (None for all fields)
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of dictionaries containing record data
        """
        if fields:
            kwargs["fields"] = fields
        return self.execute_kw(model, "search_read", [domain], kwargs)

    def fields_get(
        self, model: str, attributes: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get field definitions for a model.

        Args:
            model: The Odoo model name
            attributes: List of field attributes to return

        Returns:
            Dictionary mapping field names to their definitions
        """
        # Check cache first
        cached_fields = self._performance_manager.get_cached_fields(model)
        if cached_fields and not attributes:  # Only use cache if no specific attributes requested
            logger.debug(f"Field definitions for {model} retrieved from cache")
            return cached_fields

        # Get fields from server
        kwargs = {}
        if attributes:
            kwargs["attributes"] = attributes

        with self._performance_manager.monitor.track_operation(f"fields_get_{model}"):
            fields = self.execute_kw(model, "fields_get", [], kwargs)

        # Cache if we got all attributes
        if not attributes:
            self._performance_manager.cache_fields(model, fields)

        return fields

    def search_count(self, model: str, domain: List[Union[str, List[Any]]]) -> int:
        """Count records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter

        Returns:
            Number of records matching the domain
        """
        return self.execute_kw(model, "search_count", [domain], {})

    def create(self, model: str, values: Dict[str, Any]) -> int:
        """Create a new record.

        Args:
            model: The Odoo model name
            values: Dictionary of field values for the new record

        Returns:
            ID of the created record

        Raises:
            OdooConnectionError: If creation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"create_{model}"):
                record_id = self.execute_kw(model, "create", [values], {})
                # Invalidate cache for this model
                self._performance_manager.invalidate_record_cache(model)
                logger.info(f"Created {model} record with ID {record_id}")
                return record_id
        except Exception as e:
            logger.error(f"Failed to create {model} record: {e}")
            raise

    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool:
        """Update existing records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to update
            values: Dictionary of field values to update

        Returns:
            True if update was successful

        Raises:
            OdooConnectionError: If update fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"write_{model}"):
                result = self.execute_kw(model, "write", [ids, values], {})
                # Invalidate cache for updated records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Updated {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to update {model} records: {e}")
            raise

    def unlink(self, model: str, ids: List[int]) -> bool:
        """Delete records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to delete

        Returns:
            True if deletion was successful

        Raises:
            OdooConnectionError: If deletion fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"unlink_{model}"):
                result = self.execute_kw(model, "unlink", [ids], {})
                # Invalidate cache for deleted records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Deleted {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to delete {model} records: {e}")
            raise

    def get_server_version(self) -> Optional[Dict[str, Any]]:
        """Get Odoo server version information.

        Returns:
            Dictionary with version information or None if not connected
        """
        if not self._connected:
            return None

        try:
            return self.common_proxy.version()  # type: ignore[invalid-return-type]  # XML-RPC proxy is untyped
        except Exception as e:
            logger.error(f"Failed to get server version: {e}")
            return None


@contextmanager
def create_connection(config: OdooConfig, timeout: int = OdooConnection.DEFAULT_TIMEOUT):
    """Create a connection context manager.

    Args:
        config: OdooConfig object
        timeout: Connection timeout in seconds

    Yields:
        Connected OdooConnection instance

    Example:
        with create_connection(config) as conn:
            # Use connection
            version = conn.common_proxy.version()
    """
    conn = OdooConnection(config, timeout)
    try:
        conn.connect()
        yield conn
    finally:
        conn.disconnect()
