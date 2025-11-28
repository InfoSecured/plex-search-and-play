"""Sensor platform for Plex Search and Play."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

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
    DOMAIN,
    EVENT_SEARCH_COMPLETED,
    ICON_MOVIE,
    ICON_MUSIC,
    ICON_PLEX,
    ICON_SEARCH,
    ICON_TV,
    MEDIA_TYPE_EPISODE,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_SHOW,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Plex Search and Play sensors."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]

    # Create search status sensor
    status_sensor = PlexSearchStatusSensor(config_entry)

    # Create result sensors (slots for up to 6 results)
    result_sensors = [
        PlexSearchResultSensor(config_entry, index)
        for index in range(6)
    ]

    async_add_entities([status_sensor, *result_sensors], True)

    _LOGGER.info("Plex Search and Play sensors created")


class PlexSearchStatusSensor(SensorEntity):
    """Sensor representing search status and result count."""

    _attr_has_entity_name = True
    _attr_icon = ICON_SEARCH

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the search status sensor."""
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_search_status"
        self._attr_name = "Search Status"
        self._attr_native_value = "Ready"
        self._result_count = 0
        self._last_query = ""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        # Get selected players and libraries from integration data
        entry_data = self.hass.data[DOMAIN].get(self._config_entry.entry_id, {})
        selected_players = entry_data.get("selected_players", [])
        libraries = entry_data.get("libraries", [])

        return {
            "result_count": self._result_count,
            "last_query": self._last_query,
            "selected_players": selected_players,
            "libraries": libraries,
        }

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Listen for search events
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_SEARCH_COMPLETED,
                self._handle_search_completed
            )
        )

    @callback
    def _handle_search_completed(self, event) -> None:
        """Handle search completed event."""
        self._result_count = event.data.get("result_count", 0)
        self._last_query = event.data.get("query", "")
        self._attr_native_value = f"Found {self._result_count} results"
        self.async_write_ha_state()


class PlexSearchResultSensor(SensorEntity):
    """Sensor representing a single search result."""

    _attr_has_entity_name = True

    def __init__(self, config_entry: ConfigEntry, index: int) -> None:
        """Initialize the search result sensor."""
        self._config_entry = config_entry
        self._index = index
        self._attr_unique_id = f"{config_entry.entry_id}_result_{index}"
        self._attr_name = f"Result {index + 1}"
        self._attr_native_value = "Empty"
        self._result_data: dict[str, Any] | None = None

    @property
    def icon(self) -> str:
        """Return the icon based on media type."""
        if not self._result_data:
            return ICON_PLEX

        media_type = self._result_data.get(ATTR_MEDIA_TYPE)
        if media_type == MEDIA_TYPE_MOVIE:
            return ICON_MOVIE
        if media_type in (MEDIA_TYPE_SHOW, MEDIA_TYPE_EPISODE):
            return ICON_TV
        if media_type in ("artist", "album", "track"):
            return ICON_MUSIC
        return ICON_PLEX

    @property
    def entity_picture(self) -> str | None:
        """Return the entity picture (thumbnail)."""
        if self._result_data:
            return self._result_data.get(ATTR_THUMB)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed attributes for the search result."""
        if not self._result_data:
            return {"available": False}

        attributes = {
            "available": True,
            ATTR_RATING_KEY: self._result_data.get(ATTR_RATING_KEY),
            ATTR_MEDIA_TYPE: self._result_data.get(ATTR_MEDIA_TYPE),
            ATTR_YEAR: self._result_data.get(ATTR_YEAR),
            ATTR_SUMMARY: self._result_data.get(ATTR_SUMMARY, ""),
            ATTR_THUMB: self._result_data.get(ATTR_THUMB),
            ATTR_DURATION: self._result_data.get(ATTR_DURATION),
            ATTR_RATING: self._result_data.get(ATTR_RATING),
            ATTR_LIBRARY_SECTION_ID: self._result_data.get(ATTR_LIBRARY_SECTION_ID),
            ATTR_LIBRARY_SECTION_TITLE: self._result_data.get(ATTR_LIBRARY_SECTION_TITLE),
        }

        # Add optional attributes if present
        optional_attrs = [
            ATTR_GENRES,
            ATTR_STUDIO,
            ATTR_DIRECTOR,
            ATTR_WRITERS,
            ATTR_ACTORS,
            ATTR_PARENT_TITLE,
            ATTR_GRANDPARENT_TITLE,
            ATTR_INDEX,
            ATTR_PARENT_INDEX,
        ]

        for attr in optional_attrs:
            if attr in self._result_data:
                attributes[attr] = self._result_data[attr]

        return attributes

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Listen for search events
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_SEARCH_COMPLETED,
                self._handle_search_completed
            )
        )

    @callback
    def _handle_search_completed(self, event) -> None:
        """Handle search completed event."""
        results = event.data.get("results", [])

        if self._index < len(results):
            # This slot has a result
            self._result_data = results[self._index]
            title = self._result_data.get("title", "Unknown")

            # Format title based on media type
            media_type = self._result_data.get(ATTR_MEDIA_TYPE)
            if media_type == MEDIA_TYPE_EPISODE:
                series = self._result_data.get(ATTR_GRANDPARENT_TITLE, "")
                episode_info = self._result_data.get(ATTR_PARENT_TITLE, "")
                self._attr_native_value = f"{series} - {episode_info} - {title}"
            else:
                year = self._result_data.get(ATTR_YEAR)
                self._attr_native_value = f"{title} ({year})" if year else title
        else:
            # This slot is empty
            self._result_data = None
            self._attr_native_value = "Empty"

        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get current search results from hass data
        entry_data = self.hass.data[DOMAIN].get(self._config_entry.entry_id)
        if not entry_data:
            return

        results = entry_data.get("search_results", [])

        if self._index < len(results):
            self._result_data = results[self._index]
            title = self._result_data.get("title", "Unknown")

            # Format title based on media type
            media_type = self._result_data.get(ATTR_MEDIA_TYPE)
            if media_type == MEDIA_TYPE_EPISODE:
                series = self._result_data.get(ATTR_GRANDPARENT_TITLE, "")
                episode_info = self._result_data.get(ATTR_PARENT_TITLE, "")
                self._attr_native_value = f"{series} - {episode_info} - {title}"
            else:
                year = self._result_data.get(ATTR_YEAR)
                self._attr_native_value = f"{title} ({year})" if year else title
        else:
            self._result_data = None
            self._attr_native_value = "Empty"
