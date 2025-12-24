"""Coordinator for DayBetter light local."""

import asyncio
from collections.abc import Callable
import logging
import time
from typing import Optional

from daybetter_local_api import DayBetterController, DayBetterDevice

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_LISTENING_PORT_DEFAULT,
    CONF_MULTICAST_ADDRESS_DEFAULT,
    CONF_TARGET_PORT_DEFAULT,
    SCAN_INTERVAL,
    DEVICE_OFFLINE_THRESHOLD,
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
        self._device_state_cache = {}
        # 设备最后活跃时间
        self._device_last_active = {}
        # 设备在线状态
        self._device_online = {}
        # 设备实体回调注册表
        self._device_entity_callbacks = {}
        
        # 设备发现回调
        self._discovery_callback: Optional[Callable[[DayBetterDevice, bool], bool]] = None

    async def start(self) -> None:
        """Start the DayBetter coordinator."""
        for controller in self._controllers:
            await controller.start()
            # 发送初始查询消息
            controller.send_update_message()

    async def set_discovery_callback(
        self, callback: Callable[[DayBetterDevice, bool], bool]
    ) -> None:
        """Set discovery callback for automatic DayBetter light discovery."""
        self._discovery_callback = callback
        
        for controller in self._controllers:
            # 设置设备发现回调
            controller.set_device_discovered_callback(self._handle_device_discovery)

    def _handle_device_discovery(self, device: DayBetterDevice, is_new: bool) -> None:
        """处理设备发现"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        _LOGGER.debug("Device discovered: %s, is_new: %s", fingerprint, is_new)
        
        # 更新设备活跃时间和在线状态
        self._device_last_active[fingerprint] = current_time
        self._device_online[fingerprint] = True
        
        # 初始化或更新设备状态缓存
        self._update_device_state_cache(device)
        
        # 如果是新设备，初始化实体回调列表
        if is_new and fingerprint not in self._device_entity_callbacks:
            self._device_entity_callbacks[fingerprint] = []
        
        # 设置设备状态更新回调
        def device_update_callback(updated_device: DayBetterDevice):
            self._handle_device_update(updated_device)
        
        device.set_update_callback(device_update_callback)
        
        # 调用外部发现回调
        if self._discovery_callback:
            self._discovery_callback(device, is_new)

    def _handle_device_update(self, device: DayBetterDevice) -> None:
        """处理设备状态更新"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        # 更新最后活跃时间和在线状态
        self._device_last_active[fingerprint] = current_time
        self._device_online[fingerprint] = True
        
        # 更新状态缓存
        self._update_device_state_cache(device)
        
        # 通知所有注册的实体回调
        self._notify_entity_callbacks(fingerprint)
        
        _LOGGER.debug("Device %s updated: on=%s, brightness=%s", 
                     fingerprint, getattr(device, 'on', False), 
                     getattr(device, 'brightness', 0))

    def _update_device_state_cache(self, device: DayBetterDevice) -> None:
        """更新设备状态缓存"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        if fingerprint not in self._device_state_cache:
            self._device_state_cache[fingerprint] = {}
        
        self._device_state_cache[fingerprint].update({
            'on': getattr(device, 'on', False),
            'brightness': getattr(device, 'brightness', 0),
            'rgb_color': getattr(device, 'rgb_color', None),
            'temperature_color': getattr(device, 'temperature_color', None),
            'scene': getattr(device, 'scene', None),
            'last_updated': current_time,
            'online': True  # 标记为在线
        })

    def _notify_entity_callbacks(self, fingerprint: str) -> None:
        """通知实体回调"""
        if fingerprint in self._device_entity_callbacks:
            for callback_func in self._device_entity_callbacks[fingerprint]:
                try:
                    callback_func()
                except Exception as e:
                    _LOGGER.error("Error notifying entity for device %s: %s", fingerprint, e)

    def register_entity_callback(self, fingerprint: str, callback_func: Callable[[], None]) -> None:
        """注册实体回调"""
        if fingerprint not in self._device_entity_callbacks:
            self._device_entity_callbacks[fingerprint] = []
        
        if callback_func not in self._device_entity_callbacks[fingerprint]:
            self._device_entity_callbacks[fingerprint].append(callback_func)
            _LOGGER.debug("Registered entity callback for device %s", fingerprint)

    def unregister_entity_callback(self, fingerprint: str, callback_func: Callable[[], None]) -> None:
        """注销实体回调"""
        if fingerprint in self._device_entity_callbacks and callback_func in self._device_entity_callbacks[fingerprint]:
            self._device_entity_callbacks[fingerprint].remove(callback_func)
            _LOGGER.debug("Unregistered entity callback for device %s", fingerprint)

    def cleanup(self) -> list[asyncio.Event]:
        """Stop and cleanup the coordinator."""
        return [controller.cleanup() for controller in self._controllers]

    async def turn_on(self, device: DayBetterDevice) -> None:
        """Turn on the light."""
        try:
            await device.turn_on()
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['on'] = True
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to turn on device %s: %s", device.fingerprint, ex)
            raise

    async def turn_off(self, device: DayBetterDevice) -> None:
        """Turn off the light."""
        try:
            await device.turn_off()
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['on'] = False
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to turn off device %s: %s", device.fingerprint, ex)
            raise

    async def set_brightness(self, device: DayBetterDevice, brightness: int) -> None:
        """Set light brightness."""
        try:
            await device.set_brightness(brightness)
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['brightness'] = brightness
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to set brightness for device %s: %s", device.fingerprint, ex)
            raise

    async def set_rgb_color(
        self, device: DayBetterDevice, red: int, green: int, blue: int
    ) -> None:
        """Set light RGB color."""
        try:
            await device.set_rgb_color(red, green, blue)
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['rgb_color'] = (red, green, blue)
                self._device_state_cache[fingerprint]['temperature_color'] = None
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to set RGB color for device %s: %s", device.fingerprint, ex)
            raise

    async def set_temperature(self, device: DayBetterDevice, temperature: int) -> None:
        """Set light color in kelvin."""
        try:
            await device.set_temperature(temperature)
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['temperature_color'] = temperature
                self._device_state_cache[fingerprint]['rgb_color'] = None
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to set temperature for device %s: %s", device.fingerprint, ex)
            raise

    async def set_scene(self, device: DayBetterDevice, scene: str) -> None:
        """Set light scene."""
        try:
            await device.set_scene(scene)
            # 立即更新缓存
            fingerprint = device.fingerprint
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['scene'] = scene
                self._device_state_cache[fingerprint]['last_updated'] = time.time()
                # 通知实体
                self._notify_entity_callbacks(fingerprint)
        except Exception as ex:
            _LOGGER.error("Failed to set scene for device %s: %s", device.fingerprint, ex)
            raise

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
        
        # 发送更新消息，查询设备状态
        for controller in self._controllers:
            controller.send_update_message()
        
        # 检查设备在线状态
        offline_devices = []
        for fingerprint, last_active in list(self._device_last_active.items()):
            # 如果超过阈值时间没有活跃，认为设备离线
            if current_time - last_active > DEVICE_OFFLINE_THRESHOLD:
                if self._device_online.get(fingerprint, False):
                    self._device_online[fingerprint] = False
                    offline_devices.append(fingerprint)
                    _LOGGER.info("Device %s is now offline (no response for %d seconds)", 
                                fingerprint, int(current_time - last_active))
        
        # 如果设备离线，更新缓存中的在线状态并通知实体
        for fingerprint in offline_devices:
            if fingerprint in self._device_state_cache:
                self._device_state_cache[fingerprint]['online'] = False
                self._device_state_cache[fingerprint]['last_updated'] = current_time
                self._notify_entity_callbacks(fingerprint)
        
        return self.devices

    def is_device_online(self, fingerprint: str) -> bool:
        """检查设备是否在线"""
        # 如果设备在缓存中，检查在线状态
        if fingerprint in self._device_state_cache:
            return self._device_state_cache[fingerprint].get('online', False)
        
        # 如果不在缓存中，检查在线状态字典
        return self._device_online.get(fingerprint, False)

    def get_cached_device_state(self, fingerprint: str) -> dict:
        """获取设备缓存状态"""
        return self._device_state_cache.get(fingerprint, {})

    def get_device_by_fingerprint(self, fingerprint: str) -> Optional[DayBetterDevice]:
        """根据指纹获取设备对象"""
        for device in self.devices:
            if device.fingerprint == fingerprint:
                return device
        return None