"""Config flow for Plex Search and Play integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_LIBRARIES,
    CONF_PLEX_TOKEN,
    CONF_PLEX_URL,
    CONF_SELECTED_PLAYERS,
    DEFAULT_PORT,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
)
from .plex_api import PlexSearchAPI, PlexSearchAPIError

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    plex_url = data[CONF_PLEX_URL].rstrip("/")
    plex_token = data[CONF_PLEX_TOKEN]

    # Create API instance and test connection
    api = PlexSearchAPI(plex_url, plex_token)

    try:
        await api.async_connect()
    except PlexSearchAPIError as err:
        if err.args[0] == ERROR_INVALID_AUTH:
            raise InvalidAuth from err
        if err.args[0] == ERROR_CANNOT_CONNECT:
            raise CannotConnect from err
        raise

    # Get server name for unique ID
    server_name = api.get_server_name()

    # Get available libraries
    libraries = await api.async_get_libraries()

    return {
        "title": f"Plex: {server_name}",
        "server_name": server_name,
        "libraries": libraries,
    }


class PlexSearchPlayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Plex Search and Play."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._libraries: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - Plex server connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                # Store data for next step
                self._data = user_input
                self._libraries = info["libraries"]

                # Set unique ID based on server name
                await self.async_set_unique_id(info["server_name"].lower().replace(" ", "_"))
                self._abort_if_unique_id_configured()

                # Move to media player selection step
                return await self.async_step_players()

            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = ERROR_UNKNOWN

        # Show form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_PLEX_URL, default=f"http://192.168.1.100:{DEFAULT_PORT}"): cv.string,
                vol.Required(CONF_PLEX_TOKEN): cv.string,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "docs_url": "https://github.com/InfoSecured/plex-search-and-play#setup"
            },
        )

    async def async_step_players(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle media player selection step."""
        if user_input is not None:
            # Combine all data
            self._data[CONF_SELECTED_PLAYERS] = user_input.get(CONF_SELECTED_PLAYERS, [])
            self._data[CONF_LIBRARIES] = user_input.get(CONF_LIBRARIES, [])

            # Create entry
            return self.async_create_entry(
                title=f"Plex Search and Play",
                data=self._data,
            )

        # Get all available media players
        media_players = self.hass.states.async_entity_ids(MEDIA_PLAYER_DOMAIN)

        # Create schema with multi-select for players and libraries
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SELECTED_PLAYERS,
                    default=[],
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=MEDIA_PLAYER_DOMAIN,
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_LIBRARIES,
                    default=[],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._libraries,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="players",
            data_schema=data_schema,
            description_placeholders={
                "info": "Select which media players you want to use with Plex Search and Play, "
                        "and which Plex libraries to search."
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> PlexSearchPlayOptionsFlow:
        """Get the options flow for this handler."""
        return PlexSearchPlayOptionsFlow(config_entry)


class PlexSearchPlayOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Plex Search and Play."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current configuration - config_entry is provided by parent class
        current_players = self.config_entry.data.get(CONF_SELECTED_PLAYERS, [])
        current_libraries = self.config_entry.data.get(CONF_LIBRARIES, [])

        # Get Plex API to fetch available libraries
        plex_url = self.config_entry.data[CONF_PLEX_URL]
        plex_token = self.config_entry.data[CONF_PLEX_TOKEN]
        api = PlexSearchAPI(plex_url, plex_token)

        try:
            await api.async_connect()
            available_libraries = await api.async_get_libraries()
        except PlexSearchAPIError:
            available_libraries = current_libraries

        # Get all available media players
        media_players = self.hass.states.async_entity_ids(MEDIA_PLAYER_DOMAIN)

        # Create options schema
        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SELECTED_PLAYERS,
                    default=current_players,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=MEDIA_PLAYER_DOMAIN,
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_LIBRARIES,
                    default=current_libraries,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=available_libraries,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
