"""Data update coordinator for iDotMatrix integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MAC_ADDRESS,
    CONNECTION_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_RETRIES,
    RECONNECT_INTERVAL,
)
from idotmatrix import (
    Chronograph,
    Clock,
    Common,
    ConnectionManager,
    Effect,
    Text,
)

_LOGGER = logging.getLogger(__name__)


class IDotMatrixDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.hass = hass
        self.mac_address = entry.data[CONF_MAC_ADDRESS]
        self.device_name = entry.data[CONF_NAME]
        
        self._scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        self._max_retries = entry.options.get("retry_attempts", MAX_RETRIES)
        
        self._connection_manager = ConnectionManager()
        # Set the address for the singleton
        self._connection_manager.address = self.mac_address

        self._connected = False
        
        self._state = {
            "is_on": False,
            "brightness": 255,
            "screen_flipped": False,
            "current_mode": "clock",
            "clock_style": "classic",
            "effect_mode": "rainbow",
            "last_message": "",
        }

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library. This is a dummy update since the library does not support reading state."""
        # The library does not support reading state, so we just return our tracked state.
        # The connection is managed by the library's singleton on each command call.
        # We can check the connection status to determine availability.
        try:
            # A lightweight check to see if we can connect
            if not self._connection_manager.client or not self._connection_manager.client.is_connected:
                await self._connection_manager.connect()
                # If connect succeeds, we assume it's available.
                # The library doesn't have a separate status check.
                await self._connection_manager.disconnect()
            self._connected = True
        except Exception:
            self._connected = False
            raise UpdateFailed("Failed to connect to device to check status.")

        return self._state

    # Display control methods
    async def async_turn_on(self) -> None:
        """Turn on the display."""
        await Common().screenOn()
        self._state["is_on"] = True
        self.async_update_listeners()

    async def async_turn_off(self) -> None:
        """Turn off the display."""
        await Common().screenOff()
        self._state["is_on"] = False
        self.async_update_listeners()

    async def async_set_brightness(self, brightness: int) -> None:
        """Set the display brightness."""
        device_brightness = int((brightness / 255) * 100)
        device_brightness = max(5, min(100, device_brightness))
        
        await Common().setBrightness(brightness_percent=device_brightness)
        self._state["brightness"] = brightness
        self.async_update_listeners()

    async def async_set_screen_flip(self, flipped: bool) -> None:
        """Set screen rotation/flip."""
        await Common().flipScreen(flip=flipped)
        self._state["screen_flipped"] = flipped
        self.async_update_listeners()

    # Text display methods
    async def async_display_text(self, message: str, font_size: int = 12, color: tuple = (255, 255, 255), speed: int = 50) -> None:
        """Display text message."""
        await Text().setMode(
            text=message,
            font_size=font_size,
            text_color=color,
            speed=speed
        )
        self._state["last_message"] = message
        self._state["current_mode"] = "text"
        self.async_update_listeners()

    # Clock methods
    async def async_set_clock_mode(self, style: int) -> None:
        """Set clock display mode."""
        await Clock().setMode(style=style)
        self._state["current_mode"] = "clock"
        style_names = {0: "classic", 1: "digital", 2: "analog", 3: "minimal", 4: "colorful"}
        self._state["clock_style"] = style_names.get(style, "classic")
        self.async_update_listeners()

    async def async_sync_time(self) -> None:
        """Synchronize device time with Home Assistant."""
        now = datetime.now()
        await Common().setTime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second
        )

    # Effect methods
    async def async_display_effect(self, effect_type: int) -> None:
        """Display visual effect."""
        default_colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        await Effect().setMode(
            style=effect_type,
            rgb_values=default_colors
        )
        self._state["current_mode"] = "effect"
        effect_names = {0: "rainbow", 1: "breathing", 2: "wave", 3: "fire", 4: "snow", 5: "matrix", 6: "stars", 7: "plasma"}
        self._state["effect_mode"] = effect_names.get(effect_type, "rainbow")
        self.async_update_listeners()

    # Image display methods
    async def async_display_image(self, image_path: str) -> None:
        """Display an image or GIF."""
        _LOGGER.warning("Displaying images from a path is not yet supported by the library.")
        self._state["current_mode"] = "image"
        self.async_update_listeners()

    # Chronograph methods
    async def async_start_chronograph(self) -> None:
        """Start the chronograph."""
        await Chronograph().setMode(mode=1)
        self._state["current_mode"] = "chronograph"
        self.async_update_listeners()

    async def async_stop_chronograph(self) -> None:
        """Stop the chronograph."""
        await Chronograph().setMode(mode=2)  # Corresponds to pause
        self.async_update_listeners()

    async def async_reset_chronograph(self) -> None:
        """Reset the chronograph."""
        await Chronograph().setMode(mode=0)
        self.async_update_listeners()

    async def async_freeze_screen(self) -> None:
        """Freeze the current display."""
        await Common().freezeScreen()

    async def async_reset_device(self) -> None:
        """Reset the device."""
        await Common().reset()
        self._state.update({
            "is_on": True,
            "brightness": 255,
            "screen_flipped": False,
            "current_mode": "clock",
            "clock_style": "classic",
        })
        self.async_update_listeners()

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.mac_address)},
            "name": self.device_name,
            "manufacturer": "iDotMatrix",
            "model": "LED Display",
            "sw_version": "1.0",
            "connections": {("mac", self.mac_address)},
        }

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator and disconnect from the device."""
        _LOGGER.info("Shutting down iDotMatrix coordinator for %s", self.mac_address)
        if self._connection_manager:
            await self._connection_manager.disconnect()
