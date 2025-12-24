"""Coordinator for DayBetter light local."""

import asyncio
from collections.abc import Callable
import logging
import time

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
        
        # 设备状态缓存
        self._device_states = {}
        # 设备在线状态
        self._device_online = {}
        # 设备最后更新时间
        self._device_last_update = {}

    def _update_device_state_cache(self, device: DayBetterDevice) -> None:
        """更新设备状态缓存"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        self._device_states[fingerprint] = {
            'on': getattr(device, 'on', False),
            'brightness': getattr(device, 'brightness', 0),
            'rgb_color': getattr(device, 'rgb_color', None),
            'temperature_color': getattr(device, 'temperature_color', None),
            'last_updated': current_time
        }
        self._device_online[fingerprint] = True
        self._device_last_update[fingerprint] = current_time

    async def start(self) -> None:
        """Start the DayBetter coordinator."""
        for controller in self._controllers:
            await controller.start()
            controller.send_update_message()

    async def set_discovery_callback(
        self, callback: Callable[[DayBetterDevice, bool], bool]
    ) -> None:
        """Set discovery callback for automatic DayBetter light discovery."""

        def wrapped_callback(device: DayBetterDevice, is_new: bool) -> bool:
            if is_new:
                # 设置设备更新回调，更新状态缓存
                original_callback = getattr(device, '_update_callback', None)
                
                def device_update_callback(updated_device: DayBetterDevice):
                    # 更新状态缓存
                    self._update_device_state_cache(updated_device)
                    # 调用原始回调（如果有）
                    if original_callback:
                        original_callback(updated_device)
                
                device.set_update_callback(device_update_callback)
                # 初始化设备状态缓存
                self._update_device_state_cache(device)
            
            return callback(device, is_new)

        for controller in self._controllers:
            controller.set_device_discovered_callback(wrapped_callback)

    def cleanup(self) -> list[asyncio.Event]:
        """Stop and cleanup the coordinator."""
        return [controller.cleanup() for controller in self._controllers]

    async def turn_on(self, device: DayBetterDevice) -> None:
        """Turn on the light."""
        try:
            await device.turn_on()
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to turn on device %s: %s", device.fingerprint, ex)

    async def turn_off(self, device: DayBetterDevice) -> None:
        """Turn off the light."""
        try:
            await device.turn_off()
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to turn off device %s: %s", device.fingerprint, ex)

    async def set_brightness(self, device: DayBetterDevice, brightness: int) -> None:
        """Set light brightness."""
        try:
            await device.set_brightness(brightness)
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to set brightness for device %s: %s", device.fingerprint, ex)

    async def set_rgb_color(
        self, device: DayBetterDevice, red: int, green: int, blue: int
    ) -> None:
        """Set light RGB color."""
        try:
            await device.set_rgb_color(red, green, blue)
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to set RGB color for device %s: %s", device.fingerprint, ex)

    async def set_temperature(self, device: DayBetterDevice, temperature: int) -> None:
        """Set light color in kelvin."""
        try:
            await device.set_temperature(temperature)
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to set temperature for device %s: %s", device.fingerprint, ex)

    async def set_scene(self, device: DayBetterDevice, scene: str) -> None:
        """Set light scene."""
        try:
            await device.set_scene(scene)
            # 更新缓存状态
            self._update_device_state_cache(device)
        except Exception as ex:
            _LOGGER.warning("Failed to set scene for device %s: %s", device.fingerprint, ex)

    @property
    def devices(self) -> list[DayBetterDevice]:
        """Return a list of discovered DayBetter devices."""
        devices: list[DayBetterDevice] = []
        for controller in self._controllers:
            devices = devices + controller.devices
        return devices

    async def _async_update_data(self) -> list[DayBetterDevice]:
        """更新设备数据"""
        current_time = time.time()
        
        # 发送更新消息
        for controller in self._controllers:
            controller.send_update_message()
        
        # 检查设备在线状态
        for fingerprint, last_update in list(self._device_last_update.items()):
            # 如果超过30秒没有更新，认为设备离线
            if current_time - last_update > 30:
                self._device_online[fingerprint] = False
                _LOGGER.debug("Device %s marked as offline", fingerprint)
        
        return self.devices

    def is_device_online(self, fingerprint: str) -> bool:
        """检查设备是否在线"""
        return self._device_online.get(fingerprint, False)

    def get_device_state(self, fingerprint: str) -> dict:
        """获取设备状态（包含离线时的缓存状态）"""
        cached_state = self._device_states.get(fingerprint, {})
        
        # 如果设备在线，尝试从设备对象获取最新状态
        if self.is_device_online(fingerprint):
            # 找到对应的设备对象
            for device in self.devices:
                if device.fingerprint == fingerprint:
                    cached_state.update({
                        'on': getattr(device, 'on', cached_state.get('on', False)),
                        'brightness': getattr(device, 'brightness', cached_state.get('brightness', 0)),
                        'rgb_color': getattr(device, 'rgb_color', cached_state.get('rgb_color')),
                        'temperature_color': getattr(device, 'temperature_color', cached_state.get('temperature_color')),
                        'last_updated': time.time()
                    })
                    break
        
        return cached_state

    def get_device_by_fingerprint(self, fingerprint: str) -> DayBetterDevice | None:
        """根据指纹获取设备对象"""
        for device in self.devices:
            if device.fingerprint == fingerprint:
                return device
        return None