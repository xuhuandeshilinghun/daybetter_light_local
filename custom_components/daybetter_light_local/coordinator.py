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
        
        # 设备状态缓存 - 即使设备离线也保留
        self._device_state_cache = {}
        # 设备最后响应时间
        self._device_last_response = {}
        # 设备实体回调注册表
        self._device_entity_callbacks = {}
        # 已知设备指纹列表（即使离线也保留）
        self._known_devices = set()
        
        # 设备发现回调
        self._discovery_callback: Optional[Callable[[DayBetterDevice, bool], bool]] = None

    async def start(self) -> None:
        """Start the DayBetter coordinator."""
        for controller in self._controllers:
            await controller.start()
            controller.send_update_message()

    async def set_discovery_callback(
        self, callback: Callable[[DayBetterDevice, bool], bool]
    ) -> None:
        """Set discovery callback for automatic DayBetter light discovery."""
        self._discovery_callback = callback
        
        for controller in self._controllers:
            controller.set_device_discovered_callback(self._handle_device_discovery)

    def _handle_device_discovery(self, device: DayBetterDevice, is_new: bool) -> None:
        """处理设备发现"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        # 添加到已知设备列表
        if fingerprint not in self._known_devices:
            self._known_devices.add(fingerprint)
            _LOGGER.info("New device discovered: %s (SKU: %s)", fingerprint, device.sku)
        
        # 更新设备响应时间
        self._device_last_response[fingerprint] = current_time
        
        # 初始化或更新设备状态缓存
        self._update_device_state_cache(device)
        
        # 设置设备状态更新回调
        def device_update_callback(updated_device: DayBetterDevice):
            self._handle_device_update(updated_device)
        
        # 先移除现有的回调（如果有），然后重新设置
        device.set_update_callback(device_update_callback)
        
        # 如果是新设备且外部有发现回调，调用它
        if is_new and self._discovery_callback:
            self._discovery_callback(device, is_new)
        elif not is_new:
            # 已知设备重新上线，通知所有实体
            self._notify_device_entities(fingerprint)
            _LOGGER.debug("Device %s reconnected", fingerprint)

    def _handle_device_update(self, device: DayBetterDevice) -> None:
        """处理设备状态更新"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        # 更新设备响应时间
        self._device_last_response[fingerprint] = current_time
        
        # 更新状态缓存
        self._update_device_state_cache(device)
        
        # 通知所有注册的实体回调
        self._notify_device_entities(fingerprint)
        
        _LOGGER.debug("Device %s updated: on=%s", 
                     fingerprint, getattr(device, 'on', False))

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
            'sku': device.sku,
            'last_updated': current_time,
            'online': True  # 设备在线
        })

    def _notify_device_entities(self, fingerprint: str) -> None:
        """通知设备关联的实体更新状态"""
        if fingerprint in self._device_entity_callbacks:
            for entity_callback in self._device_entity_callbacks[fingerprint]:
                try:
                    entity_callback()
                except Exception as e:
                    _LOGGER.error("Error notifying entity for device %s: %s", fingerprint, e)

    def register_device_entity(self, fingerprint: str, entity_callback: Callable[[], None]) -> None:
        """注册设备实体回调"""
        if fingerprint not in self._device_entity_callbacks:
            self._device_entity_callbacks[fingerprint] = []
        
        if entity_callback not in self._device_entity_callbacks[fingerprint]:
            self._device_entity_callbacks[fingerprint].append(entity_callback)
            _LOGGER.debug("Registered entity callback for device %s", fingerprint)

    def unregister_device_entity(self, fingerprint: str, entity_callback: Callable[[], None]) -> None:
        """注销设备实体回调"""
        if fingerprint in self._device_entity_callbacks and entity_callback in self._device_entity_callbacks[fingerprint]:
            self._device_entity_callbacks[fingerprint].remove(entity_callback)
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
                self._notify_device_entities(fingerprint)
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
                self._notify_device_entities(fingerprint)
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
                self._notify_device_entities(fingerprint)
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
                self._notify_device_entities(fingerprint)
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
                self._notify_device_entities(fingerprint)
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
                self._notify_device_entities(fingerprint)
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
        
        # 发送更新消息
        for controller in self._controllers:
            controller.send_update_message()
        
        # 检查设备响应状态
        for fingerprint in list(self._device_last_response.keys()):
            last_response = self._device_last_response.get(fingerprint, 0)
            # 如果超过90秒没有响应，认为设备离线
            if current_time - last_response > 90:
                if fingerprint in self._device_state_cache:
                    # 标记为离线
                    if self._device_state_cache[fingerprint].get('online', False):
                        self._device_state_cache[fingerprint]['online'] = False
                        self._device_state_cache[fingerprint]['last_updated'] = current_time
                        # 通知实体设备离线
                        self._notify_device_entities(fingerprint)
                        _LOGGER.info("Device %s is now offline", fingerprint)
        
        return self.devices

    def is_device_online(self, fingerprint: str) -> bool:
        """检查设备是否在线"""
        if fingerprint in self._device_state_cache:
            return self._device_state_cache[fingerprint].get('online', False)
        return False

    def get_cached_device_state(self, fingerprint: str) -> dict:
        """获取设备缓存状态（即使设备离线）"""
        return self._device_state_cache.get(fingerprint, {})

    def get_device_by_fingerprint(self, fingerprint: str) -> Optional[DayBetterDevice]:
        """根据指纹获取设备对象"""
        for device in self.devices:
            if device.fingerprint == fingerprint:
                return device
        return None

    def get_all_known_devices(self) -> list[str]:
        """获取所有已知设备的指纹列表（包括离线的）"""
        return list(self._known_devices)

    async def monitor_devices(self):
        """监控设备状态的异步任务"""
        while True:
            try:
                # 每30秒检查一次设备状态
                await asyncio.sleep(10)
                # 触发一次更新
                await self.async_request_refresh()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                _LOGGER.error("Error in device monitoring: %s", ex)
                await asyncio.sleep(10)