"""The Plex Search and Play integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_RATING_KEY,
    CONF_LIBRARIES,
    CONF_PLEX_TOKEN,
    CONF_PLEX_URL,
    CONF_SELECTED_PLAYERS,
    DEFAULT_BROWSE_LIMIT,
    DEFAULT_BROWSE_PAGE_SIZE,
    DOMAIN,
    EVENT_PLAYBACK_FAILED,
    EVENT_PLAYBACK_STARTED,
    EVENT_SEARCH_COMPLETED,
    EVENT_SEARCH_FAILED,
    EVENT_SEARCH_STARTED,
    SERVICE_BROWSE_LIBRARY,
    SERVICE_CLEAR_RESULTS,
    SERVICE_GET_BY_GENRE,
    SERVICE_GET_COLLECTIONS,
    SERVICE_GET_ON_DECK,
    SERVICE_GET_RECENTLY_ADDED,
    SERVICE_PLAY_MEDIA as PLEX_SERVICE_PLAY_MEDIA,
    SERVICE_SEARCH,
)
from .plex_api import PlexSearchAPI, PlexSearchAPIError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas
SERVICE_SEARCH_SCHEMA = vol.Schema(
    {
        vol.Required("query"): cv.string,
        vol.Optional("limit", default=6): cv.positive_int,
    }
)

SERVICE_PLAY_MEDIA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RATING_KEY): cv.string,
        vol.Required("player_entity_id"): cv.entity_id,
    }
)

SERVICE_BROWSE_LIBRARY_SCHEMA = vol.Schema(
    {
        vol.Required("library_name"): cv.string,
        vol.Optional("start", default=0): cv.positive_int,
        vol.Optional("limit", default=DEFAULT_BROWSE_PAGE_SIZE): cv.positive_int,
        vol.Optional("sort"): cv.string,
    }
)

SERVICE_GET_ON_DECK_SCHEMA = vol.Schema(
    {
        vol.Optional("limit", default=20): cv.positive_int,
    }
)

SERVICE_GET_RECENTLY_ADDED_SCHEMA = vol.Schema(
    {
        vol.Optional("limit", default=DEFAULT_BROWSE_LIMIT): cv.positive_int,
    }
)

SERVICE_GET_BY_GENRE_SCHEMA = vol.Schema(
    {
        vol.Required("library_name"): cv.string,
        vol.Required("genre"): cv.string,
        vol.Optional("limit", default=DEFAULT_BROWSE_LIMIT): cv.positive_int,
    }
)

SERVICE_GET_COLLECTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("library_name"): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plex Search and Play from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get configuration
    plex_url = entry.data[CONF_PLEX_URL]
    plex_token = entry.data[CONF_PLEX_TOKEN]
    selected_players = entry.data.get(CONF_SELECTED_PLAYERS, [])
    libraries = entry.data.get(CONF_LIBRARIES, [])

    # Create API instance
    api = PlexSearchAPI(plex_url, plex_token)

    try:
        await api.async_connect()
    except PlexSearchAPIError as err:
        _LOGGER.error("Failed to connect to Plex server: %s", err)
        return False

    # Store API instance and config
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "selected_players": selected_players,
        "libraries": libraries,
        "search_results": [],
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    async def handle_search(call: ServiceCall) -> None:
        """Handle the search service call."""
        query = call.data["query"]
        limit = call.data.get("limit", 6)

        _LOGGER.info("Searching Plex for: %s (limit: %d)", query, limit)

        # Fire search started event
        hass.bus.async_fire(
            EVENT_SEARCH_STARTED,
            {"query": query, "limit": limit}
        )

        try:
            # Perform search
            results = await api.async_search(
                query=query,
                library_sections=libraries if libraries else None,
                limit=limit
            )

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = results

            # Fire search completed event
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "query": query,
                    "result_count": len(results),
                    "results": results
                }
            )

            _LOGGER.info("Search completed: %d results found", len(results))

        except PlexSearchAPIError as err:
            _LOGGER.error("Search failed: %s", err)
            hass.bus.async_fire(
                EVENT_SEARCH_FAILED,
                {"query": query, "error": str(err)}
            )
            raise HomeAssistantError(f"Search failed: {err}") from err

    async def handle_play_media(call: ServiceCall) -> None:
        """Handle the play_media service call."""
        rating_key = call.data[ATTR_RATING_KEY]
        player_entity_id = call.data["player_entity_id"]

        _LOGGER.info("Playing media (rating_key: %s) on %s", rating_key, player_entity_id)

        # Validate player is in selected list
        if selected_players and player_entity_id not in selected_players:
            error_msg = f"Player {player_entity_id} is not in the selected players list"
            _LOGGER.error(error_msg)
            hass.bus.async_fire(
                EVENT_PLAYBACK_FAILED,
                {
                    "rating_key": rating_key,
                    "player": player_entity_id,
                    "error": error_msg
                }
            )
            raise HomeAssistantError(error_msg)

        try:
            # Check if this is an Apple TV - they need special Plex deep linking
            player_state = hass.states.get(player_entity_id)
            is_apple_tv = (
                player_state and
                player_state.attributes.get("source") == "com.plexapp.plugins.library" or
                "apple_tv" in player_entity_id.lower()
            )

            if is_apple_tv:
                # Apple TV: Use Plex deep link format
                machine_id = api.get_machine_identifier()
                if not machine_id:
                    raise HomeAssistantError("Could not get Plex server machine identifier")

                # Plex deep link format for Apple TV
                media_url = f"plex://preplay/?metadataKey=%2Flibrary%2Fmetadata%2F{rating_key}&server={machine_id}"
                content_type = MediaType.URL
                _LOGGER.debug(
                    "Using Plex deep link for Apple TV - rating_key=%s, server=%s",
                    rating_key,
                    machine_id,
                )
            else:
                # Other players: Use direct media URL
                media_url, media_type = await api.async_get_media_url(rating_key)
                safe_url = media_url.replace(api._plex_token, "***")
                _LOGGER.debug(
                    "Resolved media URL for rating_key=%s: %s (type: %s)",
                    rating_key,
                    safe_url,
                    media_type,
                )

                # Determine the correct media content type
                if media_type == "track":
                    content_type = MediaType.MUSIC
                elif media_type in ("movie", "episode", "video"):
                    content_type = MediaType.VIDEO
                else:
                    content_type = MediaType.URL

            await hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_PLAY_MEDIA,
                {
                    "entity_id": player_entity_id,
                    ATTR_MEDIA_CONTENT_TYPE: content_type,
                    ATTR_MEDIA_CONTENT_ID: media_url,
                },
                blocking=True,
            )

            # Fire playback started event
            hass.bus.async_fire(
                EVENT_PLAYBACK_STARTED,
                {
                    "rating_key": rating_key,
                    "player": player_entity_id,
                    "media_url": media_url
                }
            )

            _LOGGER.info("Playback started successfully")

        except PlexSearchAPIError as err:
            _LOGGER.error("Failed to get media URL: %s", err)
            hass.bus.async_fire(
                EVENT_PLAYBACK_FAILED,
                {
                    "rating_key": rating_key,
                    "player": player_entity_id,
                    "error": str(err)
                }
            )
            raise HomeAssistantError(f"Failed to play media: {err}") from err

    async def handle_clear_results(call: ServiceCall) -> None:
        """Handle the clear_results service call."""
        hass.data[DOMAIN][entry.entry_id]["search_results"] = []
        _LOGGER.info("Search results cleared")

    async def handle_browse_library(call: ServiceCall) -> None:
        """Handle the browse_library service call."""
        library_name = call.data["library_name"]
        start = call.data.get("start", 0)
        limit = call.data.get("limit", DEFAULT_BROWSE_PAGE_SIZE)
        sort = call.data.get("sort")

        _LOGGER.info("Browsing library: %s (start=%d, limit=%d)", library_name, start, limit)

        try:
            result = await api.async_browse_library(
                library_name=library_name,
                start=start,
                limit=limit,
                sort=sort
            )

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = result["results"]

            # Fire event with browse results
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "library": library_name,
                    "result_count": len(result["results"]),
                    "total_count": result["total_count"],
                    "has_more": result["has_more"],
                    "results": result["results"]
                }
            )

            _LOGGER.info("Browse completed: %d results found", len(result["results"]))

        except PlexSearchAPIError as err:
            _LOGGER.error("Browse failed: %s", err)
            raise HomeAssistantError(f"Browse failed: {err}") from err

    async def handle_get_on_deck(call: ServiceCall) -> None:
        """Handle the get_on_deck service call."""
        limit = call.data.get("limit", 20)

        _LOGGER.info("Getting on deck items (limit=%d)", limit)

        try:
            results = await api.async_get_on_deck(
                library_sections=libraries if libraries else None,
                limit=limit
            )

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = results

            # Fire event
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "type": "on_deck",
                    "result_count": len(results),
                    "results": results
                }
            )

            _LOGGER.info("On deck completed: %d results found", len(results))

        except PlexSearchAPIError as err:
            _LOGGER.error("Get on deck failed: %s", err)
            raise HomeAssistantError(f"Get on deck failed: {err}") from err

    async def handle_get_recently_added(call: ServiceCall) -> None:
        """Handle the get_recently_added service call."""
        limit = call.data.get("limit", DEFAULT_BROWSE_LIMIT)

        _LOGGER.info("Getting recently added items (limit=%d)", limit)

        try:
            results = await api.async_get_recently_added(
                library_sections=libraries if libraries else None,
                limit=limit
            )

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = results

            # Fire event
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "type": "recently_added",
                    "result_count": len(results),
                    "results": results
                }
            )

            _LOGGER.info("Recently added completed: %d results found", len(results))

        except PlexSearchAPIError as err:
            _LOGGER.error("Get recently added failed: %s", err)
            raise HomeAssistantError(f"Get recently added failed: {err}") from err

    async def handle_get_by_genre(call: ServiceCall) -> None:
        """Handle the get_by_genre service call."""
        library_name = call.data["library_name"]
        genre = call.data["genre"]
        limit = call.data.get("limit", DEFAULT_BROWSE_LIMIT)

        _LOGGER.info("Getting items by genre: %s from %s (limit=%d)", genre, library_name, limit)

        try:
            results = await api.async_get_by_genre(
                library_name=library_name,
                genre=genre,
                limit=limit
            )

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = results

            # Fire event
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "type": "genre",
                    "genre": genre,
                    "library": library_name,
                    "result_count": len(results),
                    "results": results
                }
            )

            _LOGGER.info("Genre browse completed: %d results found", len(results))

        except PlexSearchAPIError as err:
            _LOGGER.error("Get by genre failed: %s", err)
            raise HomeAssistantError(f"Get by genre failed: {err}") from err

    async def handle_get_collections(call: ServiceCall) -> None:
        """Handle the get_collections service call."""
        library_name = call.data["library_name"]

        _LOGGER.info("Getting collections from: %s", library_name)

        try:
            results = await api.async_get_collections(library_name=library_name)

            # Store results
            hass.data[DOMAIN][entry.entry_id]["search_results"] = results

            # Fire event
            hass.bus.async_fire(
                EVENT_SEARCH_COMPLETED,
                {
                    "type": "collections",
                    "library": library_name,
                    "result_count": len(results),
                    "results": results
                }
            )

            _LOGGER.info("Collections retrieved: %d found", len(results))

        except PlexSearchAPIError as err:
            _LOGGER.error("Get collections failed: %s", err)
            raise HomeAssistantError(f"Get collections failed: {err}") from err

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        handle_search,
        schema=SERVICE_SEARCH_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        PLEX_SERVICE_PLAY_MEDIA,
        handle_play_media,
        schema=SERVICE_PLAY_MEDIA_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_RESULTS,
        handle_clear_results,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BROWSE_LIBRARY,
        handle_browse_library,
        schema=SERVICE_BROWSE_LIBRARY_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ON_DECK,
        handle_get_on_deck,
        schema=SERVICE_GET_ON_DECK_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECENTLY_ADDED,
        handle_get_recently_added,
        schema=SERVICE_GET_RECENTLY_ADDED_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_BY_GENRE,
        handle_get_by_genre,
        schema=SERVICE_GET_BY_GENRE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_COLLECTIONS,
        handle_get_collections,
        schema=SERVICE_GET_COLLECTIONS_SCHEMA,
    )

    _LOGGER.info("Plex Search and Play integration setup complete")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove services (only if this is the last entry)
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) == 1:  # This is the last entry
            hass.services.async_remove(DOMAIN, SERVICE_SEARCH)
            hass.services.async_remove(DOMAIN, PLEX_SERVICE_PLAY_MEDIA)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_RESULTS)
            hass.services.async_remove(DOMAIN, SERVICE_BROWSE_LIBRARY)
            hass.services.async_remove(DOMAIN, SERVICE_GET_ON_DECK)
            hass.services.async_remove(DOMAIN, SERVICE_GET_RECENTLY_ADDED)
            hass.services.async_remove(DOMAIN, SERVICE_GET_BY_GENRE)
            hass.services.async_remove(DOMAIN, SERVICE_GET_COLLECTIONS)

        # Remove data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
