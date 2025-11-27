"""Plex API wrapper for search and playback functionality."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from typing import Any

from plexapi.exceptions import BadRequest, NotFound, Unauthorized
from plexapi.server import PlexServer
from plexapi.video import Episode, Movie, Show

from homeassistant.exceptions import HomeAssistantError

from .const import (
    ATTR_ACTORS,
    ATTR_DIRECTOR,
    ATTR_DURATION,
    ATTR_GENRES,
    ATTR_GRANDPARENT_TITLE,
    ATTR_INDEX,
    ATTR_LIBRARY_SECTION_ID,
    ATTR_LIBRARY_SECTION_TITLE,
    ATTR_MEDIA_TYPE,
    ATTR_PARENT_INDEX,
    ATTR_PARENT_TITLE,
    ATTR_RATING,
    ATTR_RATING_KEY,
    ATTR_STUDIO,
    ATTR_SUMMARY,
    ATTR_THUMB,
    ATTR_WRITERS,
    ATTR_YEAR,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_TIMEOUT,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_NO_RESULTS,
    MEDIA_TYPE_EPISODE,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_SEASON,
    MEDIA_TYPE_SHOW,
)

_LOGGER = logging.getLogger(__name__)


class PlexSearchAPIError(HomeAssistantError):
    """Exception raised for Plex API errors."""


class PlexSearchAPI:
    """Interface to communicate with Plex server for search and playback."""

    def __init__(self, plex_url: str, plex_token: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        """Initialize the Plex API wrapper.

        Args:
            plex_url: The base URL of the Plex server (e.g., http://192.168.1.100:32400)
            plex_token: The authentication token for the Plex server
            timeout: Request timeout in seconds
        """
        self._plex_url = plex_url
        self._plex_token = plex_token
        self._timeout = timeout
        self._server: PlexServer | None = None
        self._machine_identifier: str | None = None

    def _connect_blocking(self) -> PlexServer:
        """Blocking call to connect to Plex server.

        This runs in an executor to avoid blocking the event loop.
        """
        return PlexServer(
            self._plex_url,
            self._plex_token,
            timeout=self._timeout
        )

    async def async_connect(self) -> bool:
        """Test connection to Plex server.

        Returns:
            True if connection successful

        Raises:
            PlexSearchAPIError: If connection fails
        """
        try:
            # Run blocking PlexServer call in executor
            loop = asyncio.get_event_loop()
            self._server = await loop.run_in_executor(None, self._connect_blocking)

            # Test the connection and get machine identifier
            _ = self._server.friendlyName
            self._machine_identifier = self._server.machineIdentifier
            _LOGGER.info("Successfully connected to Plex server: %s", self._server.friendlyName)
            return True
        except Unauthorized as err:
            _LOGGER.error("Invalid Plex token")
            raise PlexSearchAPIError(ERROR_INVALID_AUTH) from err
        except (BadRequest, ConnectionError) as err:
            _LOGGER.error("Cannot connect to Plex server at %s", self._plex_url)
            raise PlexSearchAPIError(ERROR_CANNOT_CONNECT) from err
        except Exception as err:
            _LOGGER.exception("Unexpected error connecting to Plex: %s", err)
            raise PlexSearchAPIError(ERROR_CANNOT_CONNECT) from err

    def get_server_name(self) -> str:
        """Get the friendly name of the Plex server."""
        if self._server:
            return self._server.friendlyName
        return "Unknown"

    def get_machine_identifier(self) -> str | None:
        """Get the machine identifier of the Plex server."""
        return self._machine_identifier

    def _search_blocking(
        self,
        query: str,
        library_sections: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[Any]:
        """Blocking call to search Plex library.

        This runs in an executor to avoid blocking the event loop.
        """
        results = []

        if library_sections:
            # Search specific library sections
            for section_name in library_sections:
                try:
                    section = self._server.library.section(section_name)
                    section_results = section.search(query, limit=limit)
                    results.extend(section_results)
                except NotFound:
                    _LOGGER.warning("Library section '%s' not found", section_name)
                    continue
        else:
            # Search all libraries
            results = self._server.library.search(query, limit=limit)

        return results

    async def async_search(
        self,
        query: str,
        library_sections: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[dict[str, Any]]:
        """Search Plex library for media.

        Args:
            query: Search query string
            library_sections: Optional list of library section names to search
            limit: Maximum number of results to return

        Returns:
            List of media items with metadata

        Raises:
            PlexSearchAPIError: If search fails
        """
        if not self._server:
            await self.async_connect()

        try:
            # Run blocking search in executor
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                partial(self._search_blocking, query, library_sections, limit)
            )

            if not results:
                _LOGGER.info("No results found for query: %s", query)
                return []

            # Parse and format results in executor to avoid blocking the event loop
            formatted_results = await asyncio.to_thread(
                self._format_results_blocking, results[:limit]
            )

            _LOGGER.info("Found %d results for query: %s", len(formatted_results), query)
            return formatted_results

        except Exception as err:
            _LOGGER.exception("Error searching Plex: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    def _format_media_item(self, item: Any, index: int) -> dict[str, Any]:
        """Format a Plex media item into a standardized dictionary.

        Args:
            item: Plex media item
            index: Index in the results list

        Returns:
            Formatted media item dictionary
        """
        # Common attributes
        formatted = {
            "index": index,
            ATTR_RATING_KEY: str(item.ratingKey),
            "title": item.title,
            ATTR_SUMMARY: getattr(item, "summary", ""),
            ATTR_THUMB: self._get_thumb_url(item),
            ATTR_YEAR: getattr(item, "year", None),
            ATTR_RATING: getattr(item, "rating", None),
            ATTR_DURATION: getattr(item, "duration", 0),
            ATTR_LIBRARY_SECTION_ID: item.librarySectionID,
            ATTR_LIBRARY_SECTION_TITLE: item.librarySectionTitle,
        }

        # Media type specific attributes
        if isinstance(item, Movie):
            formatted[ATTR_MEDIA_TYPE] = MEDIA_TYPE_MOVIE
            formatted[ATTR_STUDIO] = getattr(item, "studio", None)
            formatted[ATTR_DIRECTOR] = self._get_directors(item)
            formatted[ATTR_WRITERS] = self._get_writers(item)
            formatted[ATTR_ACTORS] = self._get_actors(item)
            formatted[ATTR_GENRES] = self._get_genres(item)

        elif isinstance(item, Show):
            formatted[ATTR_MEDIA_TYPE] = MEDIA_TYPE_SHOW
            formatted[ATTR_STUDIO] = getattr(item, "studio", None)
            formatted[ATTR_GENRES] = self._get_genres(item)

        elif isinstance(item, Episode):
            formatted[ATTR_MEDIA_TYPE] = MEDIA_TYPE_EPISODE
            formatted[ATTR_PARENT_TITLE] = getattr(item, "seasonEpisode", "")  # e.g., "S01E05"
            formatted[ATTR_GRANDPARENT_TITLE] = getattr(item, "grandparentTitle", "")  # Show name
            formatted[ATTR_INDEX] = getattr(item, "index", None)  # Episode number
            formatted[ATTR_PARENT_INDEX] = getattr(item, "parentIndex", None)  # Season number
            formatted[ATTR_DIRECTOR] = self._get_directors(item)
            formatted[ATTR_WRITERS] = self._get_writers(item)

        else:
            # Generic handling for other types
            formatted[ATTR_MEDIA_TYPE] = item.type

        return formatted

    def _get_thumb_url(self, item: Any) -> str:
        """Get the full URL for the item's thumbnail.

        Args:
            item: Plex media item

        Returns:
            Full thumbnail URL
        """
        if not hasattr(item, "thumb") or not item.thumb:
            return ""

        # Return full URL including the server base URL
        return f"{self._plex_url}{item.thumb}?X-Plex-Token={self._plex_token}"

    def _format_results_blocking(self, items: list[Any]) -> list[dict[str, Any]]:
        """Format a list of Plex media items in a worker thread."""
        formatted_results: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            try:
                formatted_item = self._format_media_item(item, idx)
                formatted_results.append(formatted_item)
            except Exception as err:
                _LOGGER.warning("Error formatting media item: %s", err)
                continue
        return formatted_results

    def _get_genres(self, item: Any) -> list[str]:
        """Extract genre names from a media item."""
        try:
            return [genre.tag for genre in item.genres] if hasattr(item, "genres") else []
        except Exception:
            return []

    def _get_directors(self, item: Any) -> list[str]:
        """Extract director names from a media item."""
        try:
            return [director.tag for director in item.directors] if hasattr(item, "directors") else []
        except Exception:
            return []

    def _get_writers(self, item: Any) -> list[str]:
        """Extract writer names from a media item."""
        try:
            return [writer.tag for writer in item.writers] if hasattr(item, "writers") else []
        except Exception:
            return []

    def _get_actors(self, item: Any) -> list[str]:
        """Extract actor names from a media item (limited to first 5)."""
        try:
            if hasattr(item, "roles"):
                return [actor.tag for actor in item.roles[:5]]
            return []
        except Exception:
            return []

    async def async_get_media_url(self, rating_key: str) -> tuple[str, str]:
        """Get the playback URL and media type for a media item.

        Args:
            rating_key: The Plex rating key for the media item

        Returns:
            Tuple of (playback URL, media type string)

        Raises:
            PlexSearchAPIError: If media item not found
        """
        if not self._server:
            await self.async_connect()

        try:
            return await asyncio.to_thread(self._get_media_url_blocking, rating_key)
        except NotFound as err:
            _LOGGER.error("Media item with rating key %s not found", rating_key)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err
        except Exception as err:
            _LOGGER.exception("Error getting media URL: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    def _get_media_url_blocking(self, rating_key: str) -> tuple[str, str]:
        """Blocking helper to get media URL and type (runs in executor thread)."""
        item = self._server.fetchItem(int(rating_key))
        media_type = getattr(item, "type", "video")

        # Use direct file path for better compatibility with media players
        # Most players handle direct URLs better than transcoded streams
        if hasattr(item, "media") and item.media:
            media = item.media[0]
            if hasattr(media, "parts") and media.parts:
                part = media.parts[0]
                key = part.key
                return f"{self._plex_url}{key}?X-Plex-Token={self._plex_token}", media_type

        # Fallback to transcoded stream URL if direct path not available
        try:
            stream_url = item.getStreamURL(
                token=self._plex_token,
                protocol="http",
                maxVideoBitrate=None,
            )
            if stream_url:
                return stream_url, media_type
        except Exception as err:
            _LOGGER.warning("Failed to get stream URL: %s", err)

        raise PlexSearchAPIError("No playable media found")

    def _get_libraries_blocking(self) -> list[str]:
        """Blocking call to get library sections.

        This runs in an executor to avoid blocking the event loop.
        """
        return [section.title for section in self._server.library.sections()]

    async def async_get_libraries(self) -> list[str]:
        """Get list of available library section names (async version).

        Returns:
            List of library section names
        """
        if not self._server:
            return []

        try:
            loop = asyncio.get_event_loop()
            libraries = await loop.run_in_executor(None, self._get_libraries_blocking)
            return libraries
        except Exception as err:
            _LOGGER.exception("Error getting libraries: %s", err)
            return []

    def get_libraries(self) -> list[str]:
        """Get list of available library section names (sync version - deprecated).

        Note: This is kept for backward compatibility but should not be called
        from async contexts. Use async_get_libraries() instead.

        Returns:
            List of library section names
        """
        if not self._server:
            return []

        try:
            return self._get_libraries_blocking()
        except Exception as err:
            _LOGGER.exception("Error getting libraries: %s", err)
            return []

    async def async_browse_library(
        self,
        library_name: str,
        start: int = 0,
        limit: int = 50,
        sort: str | None = None
    ) -> dict[str, Any]:
        """Browse a Plex library section.

        Args:
            library_name: Name of the library section to browse
            start: Starting index for pagination
            limit: Number of items to return
            sort: Sort order (e.g., "titleSort", "addedAt:desc", "year:desc")

        Returns:
            Dictionary with results and pagination info

        Raises:
            PlexSearchAPIError: If browse fails
        """
        if not self._server:
            await self.async_connect()

        try:
            section = self._server.library.section(library_name)

            # Get all items with optional sorting
            if sort:
                all_items = section.all(sort=sort)
            else:
                all_items = section.all()

            total_count = len(all_items)

            # Paginate results
            paginated_items = all_items[start:start + limit]

            # Format items
            formatted_results = []
            for idx, item in enumerate(paginated_items):
                try:
                    formatted_item = self._format_media_item(item, start + idx)
                    formatted_results.append(formatted_item)
                except Exception as err:
                    _LOGGER.warning("Error formatting media item: %s", err)
                    continue

            _LOGGER.info(
                "Browsing library '%s': returned %d items (start=%d, total=%d)",
                library_name, len(formatted_results), start, total_count
            )

            return {
                "results": formatted_results,
                "total_count": total_count,
                "start": start,
                "limit": limit,
                "has_more": (start + limit) < total_count
            }

        except NotFound as err:
            _LOGGER.error("Library section '%s' not found", library_name)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err
        except Exception as err:
            _LOGGER.exception("Error browsing library: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    async def async_get_on_deck(
        self,
        library_sections: list[str] | None = None,
        limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get on deck (in progress) media items.

        Args:
            library_sections: Optional list of library sections to filter
            limit: Maximum number of items to return

        Returns:
            List of on deck media items

        Raises:
            PlexSearchAPIError: If operation fails
        """
        if not self._server:
            await self.async_connect()

        try:
            results = []

            if library_sections:
                # Get on deck from specific sections
                for section_name in library_sections:
                    try:
                        section = self._server.library.section(section_name)
                        on_deck_items = section.onDeck()
                        results.extend(on_deck_items)
                    except NotFound:
                        _LOGGER.warning("Library section '%s' not found", section_name)
                        continue
            else:
                # Get on deck from all libraries
                results = self._server.library.onDeck()

            # Format and limit results
            formatted_results = []
            for idx, item in enumerate(results[:limit]):
                try:
                    formatted_item = self._format_media_item(item, idx)
                    # Add view offset for continue watching
                    if hasattr(item, "viewOffset"):
                        formatted_item["view_offset"] = item.viewOffset
                    formatted_results.append(formatted_item)
                except Exception as err:
                    _LOGGER.warning("Error formatting on deck item: %s", err)
                    continue

            _LOGGER.info("Found %d on deck items", len(formatted_results))
            return formatted_results

        except Exception as err:
            _LOGGER.exception("Error getting on deck items: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    async def async_get_recently_added(
        self,
        library_sections: list[str] | None = None,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get recently added media items.

        Args:
            library_sections: Optional list of library sections to filter
            limit: Maximum number of items to return

        Returns:
            List of recently added media items

        Raises:
            PlexSearchAPIError: If operation fails
        """
        if not self._server:
            await self.async_connect()

        try:
            results = []

            if library_sections:
                # Get recently added from specific sections
                for section_name in library_sections:
                    try:
                        section = self._server.library.section(section_name)
                        recent_items = section.recentlyAdded(maxresults=limit)
                        results.extend(recent_items)
                    except NotFound:
                        _LOGGER.warning("Library section '%s' not found", section_name)
                        continue
            else:
                # Get recently added from all libraries
                results = self._server.library.recentlyAdded(maxresults=limit)

            # Format results
            formatted_results = []
            for idx, item in enumerate(results[:limit]):
                try:
                    formatted_item = self._format_media_item(item, idx)
                    # Add added date if available
                    if hasattr(item, "addedAt"):
                        formatted_item["added_at"] = item.addedAt.isoformat()
                    formatted_results.append(formatted_item)
                except Exception as err:
                    _LOGGER.warning("Error formatting recently added item: %s", err)
                    continue

            _LOGGER.info("Found %d recently added items", len(formatted_results))
            return formatted_results

        except Exception as err:
            _LOGGER.exception("Error getting recently added items: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    async def async_get_by_genre(
        self,
        library_name: str,
        genre: str,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get media items filtered by genre.

        Args:
            library_name: Name of the library section
            genre: Genre to filter by
            limit: Maximum number of items to return

        Returns:
            List of media items in the specified genre

        Raises:
            PlexSearchAPIError: If operation fails
        """
        if not self._server:
            await self.async_connect()

        try:
            section = self._server.library.section(library_name)

            # Search by genre
            results = section.search(genre=genre, limit=limit)

            # Format results
            formatted_results = []
            for idx, item in enumerate(results):
                try:
                    formatted_item = self._format_media_item(item, idx)
                    formatted_results.append(formatted_item)
                except Exception as err:
                    _LOGGER.warning("Error formatting genre item: %s", err)
                    continue

            _LOGGER.info(
                "Found %d items in genre '%s' from library '%s'",
                len(formatted_results), genre, library_name
            )
            return formatted_results

        except NotFound as err:
            _LOGGER.error("Library section '%s' not found", library_name)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err
        except Exception as err:
            _LOGGER.exception("Error getting items by genre: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err

    async def async_get_collections(
        self,
        library_name: str
    ) -> list[dict[str, Any]]:
        """Get collections from a library.

        Args:
            library_name: Name of the library section

        Returns:
            List of collections with basic info

        Raises:
            PlexSearchAPIError: If operation fails
        """
        if not self._server:
            await self.async_connect()

        try:
            section = self._server.library.section(library_name)
            collections = section.collections()

            formatted_collections = []
            for idx, collection in enumerate(collections):
                try:
                    formatted_collection = {
                        "index": idx,
                        ATTR_RATING_KEY: str(collection.ratingKey),
                        "title": collection.title,
                        ATTR_SUMMARY: getattr(collection, "summary", ""),
                        ATTR_THUMB: self._get_thumb_url(collection),
                        "child_count": getattr(collection, "childCount", 0),
                        ATTR_MEDIA_TYPE: "collection"
                    }
                    formatted_collections.append(formatted_collection)
                except Exception as err:
                    _LOGGER.warning("Error formatting collection: %s", err)
                    continue

            _LOGGER.info("Found %d collections in library '%s'", len(formatted_collections), library_name)
            return formatted_collections

        except NotFound as err:
            _LOGGER.error("Library section '%s' not found", library_name)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err
        except Exception as err:
            _LOGGER.exception("Error getting collections: %s", err)
            raise PlexSearchAPIError(ERROR_NO_RESULTS) from err
