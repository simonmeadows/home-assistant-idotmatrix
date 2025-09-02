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
        
        self._connection_manager = None
        self._device = None
        self._connected = False
        self._command_lock = asyncio.Lock()
        self._retry_count = 0
        
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

    def _fire_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire a device automation event."""
        event_data = {
            "device_id": self.entry.entry_id,
            "mac_address": self.mac_address,
        }
        if data:
            event_data.update(data)
        
        self.hass.bus.async_fire(f"{DOMAIN}_{event_type}", event_data)

    async def async_connect(self) -> bool:
        """Connect to the device."""
        try:
            self._connection_manager = ConnectionManager()
            await self._connection_manager.connectByAddress(self.mac_address)
            self._device = self._connection_manager.client
            
            if self._device:
                self._connected = True
                self._retry_count = 0
                _LOGGER.info("Connected to iDotMatrix device %s", self.mac_address)
                return True
            else:
                _LOGGER.error("Failed to connect to device %s", self.mac_address)
                return False
                
        except Exception as ex:
            _LOGGER.exception("Error connecting to device %s: %s", self.mac_address, ex)
            return False

    async def async_disconnect(self) -> None:
        """Disconnect from the device."""
        if self._device and self._connected:
            try:
                await self._device.disconnect()
            except Exception as ex:
                _LOGGER.error("Error disconnecting from device: %s", ex)
            finally:
                self._connected = False
                self._device = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        if not self._connected:
            if self._retry_count < self._max_retries:
                _LOGGER.warning("Device disconnected, attempting to reconnect...")
                if await self.async_connect():
                    self._retry_count = 0
                else:
                    self._retry_count += 1
            else:
                raise UpdateFailed(f"Failed to connect to device after {self._max_retries} attempts")
        
        return self._state.copy()

    async def _async_send_command(self, command_func, *args, **kwargs) -> bool:
        """Send a command to the device with error handling."""
        async with self._command_lock:
            if not self._connected or not self._device:
                _LOGGER.error("Device not connected")
                return False
            
            try:
                # The library methods now handle the sending internally
                await command_func(*args, **kwargs)
                return True
            except Exception as ex:
                _LOGGER.error("Error sending command: %s", ex)
                # Don't assume disconnection on every error
                # self._connected = False
                return False

    # Display control methods
    async def async_turn_on(self) -> bool:
        """Turn on the display."""
        success = await self._async_send_command(
            Common().screenOn
        )
        if success:
            self._state["is_on"] = True
            self._fire_event("display_on")
        return success

    async def async_turn_off(self) -> bool:
        """Turn off the display."""
        success = await self._async_send_command(
            Common().screenOff
        )
        if success:
            self._state["is_on"] = False
            self._fire_event("display_off")
            self._fire_event("turned_off")
        return success

    async def async_set_brightness(self, brightness: int) -> bool:
        """Set the display brightness."""
        device_brightness = int((brightness / 255) * 100)
        # Ensure brightness is within the library's accepted range
        device_brightness = max(5, min(100, device_brightness))
        
        success = await self._async_send_command(
            Common().setBrightness,
            brightness_percent=device_brightness
        )
        if success:
            self._state["brightness"] = brightness
            self._fire_event("brightness_changed", {"brightness": brightness})
        return success

    async def async_set_screen_flip(self, flipped: bool) -> bool:
        """Set screen rotation/flip."""
        success = await self._async_send_command(
            Common().flipScreen,
            flip=flipped
        )
        if success:
            self._state["screen_flipped"] = flipped
            self._fire_event("screen_flipped", {"flipped": flipped})
        return success

    # Text display methods
    async def async_display_text(self, message: str, font_size: int = 12, color: tuple = (255, 255, 255), speed: int = 50) -> bool:
        """Display text message."""
        success = await self._async_send_command(
            Text().setMode,
            text=message,
            font_size=font_size,
            text_color=color,
            speed=speed
        )
        if success:
            self._state["last_message"] = message
            self._state["current_mode"] = "text"
            self._fire_event("text_displayed", {"message": message})
        return success

    # Clock methods
    async def async_set_clock_mode(self, style: int) -> bool:
        """Set clock display mode."""
        success = await self._async_send_command(
            Clock().setMode,
            style=style
        )
        if success:
            self._state["current_mode"] = "clock"
            style_names = {0: "classic", 1: "digital", 2: "analog", 3: "minimal", 4: "colorful"}
            self._state["clock_style"] = style_names.get(style, "classic")
            self._fire_event("clock_mode_set", {"style": style})
        return success

    async def async_sync_time(self) -> bool:
        """Synchronize device time with Home Assistant."""
        now = datetime.now()
        success = await self._async_send_command(
            Common().setTime,
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second
        )
        return success

    # Effect methods
    async def async_display_effect(self, effect_type: int, duration: int = 10, speed: int = 50) -> bool:
        """Display visual effect."""
        # The new library has a different signature. We'll use a default color list.
        default_colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        success = await self._async_send_command(
            Effect().setMode,
            style=effect_type,
            rgb_values=default_colors
        )
        if success:
            self._state["current_mode"] = "effect"
            effect_names = {0: "rainbow", 1: "breathing", 2: "wave", 3: "fire", 4: "snow", 5: "matrix", 6: "stars", 7: "plasma"}
            self._state["effect_mode"] = effect_names.get(effect_type, "rainbow")
            self._fire_event("effect_displayed", {"effect_type": effect_type})
        return success

    # Image display methods
    async def async_display_image(self, image_path: str, duration: int = 5) -> bool:
        """Display an image or GIF."""
        _LOGGER.warning("Displaying images from a path is not yet supported by the library.")
        self._state["current_mode"] = "image"
        self._fire_event("image_displayed", {"image_path": image_path})
        return True

    # Chronograph methods
    async def async_start_chronograph(self) -> bool:
        """Start the chronograph."""
        success = await self._async_send_command(
            Chronograph().setMode, mode=1
        )
        if success:
            self._state["current_mode"] = "chronograph"
            self._fire_event("chronograph_started")
        return success

    async def async_stop_chronograph(self) -> bool:
        """Stop the chronograph."""
        success = await self._async_send_command(
            Chronograph().setMode, mode=2  # Corresponds to pause
        )
        if success:
            self._fire_event("chronograph_stopped")
        return success

    async def async_reset_chronograph(self) -> bool:
        """Reset the chronograph."""
        success = await self._async_send_command(
            Chronograph().setMode, mode=0
        )
        if success:
            self._fire_event("chronograph_reset")
        return success

    async def async_freeze_screen(self) -> bool:
        """Freeze the current display."""
        success = await self._async_send_command(
            Common().freezeScreen
        )
        return success

    async def async_reset_device(self) -> bool:
        """Reset the device."""
        success = await self._async_send_command(
            Common().reset
        )
        if success:
            self._state.update({
                "is_on": True,
                "brightness": 255,
                "screen_flipped": False,
                "current_mode": "clock",
                "clock_style": "classic",
            })
            self._fire_event("device_reset")
        return success

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
        await self.async_disconnect()
