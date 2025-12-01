"""Coordinator for DayBetter light local."""

import asyncio
from collections.abc import Callable
import logging

from daybetter_local_api import DayBetterController, DayBetterDevice

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_LISTENING_PORT_DEFAULT,
    CONF_MULTICAST_ADDRESS_DEFAULT,
    CONF_TARGET_PORT_DEFAULT,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type DayBetterLocalConfigEntry = ConfigEntry[DayBetterLocalApiCoordinator]


class DayBetterLocalApiCoordinator(DataUpdateCoordinator[list[DayBetterDevice]]):
    """DayBetter light local coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: DayBetterLocalConfigEntry,
        source_ips: set[str],
    ) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name="DayBetterLightLocalApi",
            update_interval=SCAN_INTERVAL,
        )

        self._controllers: list[DayBetterController] = [
            DayBetterController(
                loop=hass.loop,
                logger=_LOGGER,
                listening_address=source_ip,
                broadcast_address=CONF_MULTICAST_ADDRESS_DEFAULT,
                broadcast_port=CONF_TARGET_PORT_DEFAULT,
                listening_port=CONF_LISTENING_PORT_DEFAULT,
                discovery_enabled=True,
                discovery_interval=1,
                update_enabled=False,
            )
            for source_ip in source_ips
        ]

    async def start(self) -> None:
        """Start the DayBetter coordinator."""

        for controller in self._controllers:
            await controller.start()
            controller.send_update_message()

    async def set_discovery_callback(
        self, callback: Callable[[DayBetterDevice, bool], bool]
    ) -> None:
        """Set discovery callback for automatic DayBetter light discovery."""

        for controller in self._controllers:
            controller.set_device_discovered_callback(callback)

    def cleanup(self) -> list[asyncio.Event]:
        """Stop and cleanup the coordinator."""

        return [controller.cleanup() for controller in self._controllers]

    async def turn_on(self, device: DayBetterDevice) -> None:
        """Turn on the light."""
        await device.turn_on()

    async def turn_off(self, device: DayBetterDevice) -> None:
        """Turn off the light."""
        await device.turn_off()

    async def set_brightness(self, device: DayBetterDevice, brightness: int) -> None:
        """Set light brightness."""
        await device.set_brightness(brightness)

    async def set_rgb_color(
        self, device: DayBetterDevice, red: int, green: int, blue: int
    ) -> None:
        """Set light RGB color."""
        await device.set_rgb_color(red, green, blue)

    async def set_temperature(self, device: DayBetterDevice, temperature: int) -> None:
        """Set light color in kelvin."""
        await device.set_temperature(temperature)

    async def set_scene(self, device: DayBetterController, scene: str) -> None:
        """Set light scene."""
        await device.set_scene(scene)

    @property
    def devices(self) -> list[DayBetterDevice]:
        """Return a list of discovered DayBetter devices."""

        devices: list[DayBetterDevice] = []
        for controller in self._controllers:
            devices = devices + controller.devices
        return devices

    async def _async_update_data(self) -> list[DayBetterDevice]:
        for controller in self._controllers:
            controller.send_update_message()
        return self.devices