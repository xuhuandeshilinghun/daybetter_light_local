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
        
        # 设备状态缓存
        self._device_state_cache = {}
        # 设备在线状态 - 用于标记设备是否响应
        self._device_responded = {}
        # 设备最后响应时间
        self._device_last_response = {}
        # 设备实体列表，用于通知状态更新
        self._device_entities = {}
        
        # 设置控制器消息回调
        for controller in self._controllers:
            controller.set_message_callback(self._handle_device_message)

    @callback
    def _handle_device_message(self, device_fingerprint: str, message_type: str, data: dict) -> None:
        """处理设备发送的UDP消息"""
        _LOGGER.debug("Received UDP message from device %s: %s", device_fingerprint, message_type)
        
        current_time = time.time()
        
        # 更新设备响应时间
        self._device_last_response[device_fingerprint] = current_time
        self._device_responded[device_fingerprint] = True
        
        # 根据消息类型更新状态缓存
        if message_type == "status_update":
            self._update_device_state_from_message(device_fingerprint, data)
        
        # 通知关联的实体
        self._notify_device_entities(device_fingerprint)

    def _update_device_state_from_message(self, fingerprint: str, data: dict) -> None:
        """从UDP消息更新设备状态缓存"""
        if fingerprint not in self._device_state_cache:
            self._device_state_cache[fingerprint] = {}
        
        self._device_state_cache[fingerprint].update({
            'on': data.get('power', False),
            'brightness': data.get('brightness', 0),
            'rgb_color': data.get('rgb_color'),
            'temperature_color': data.get('temperature'),
            'scene': data.get('scene'),
            'last_updated': time.time(),
            'source': 'udp'
        })
        
        _LOGGER.debug("Updated state cache for device %s: %s", fingerprint, 
                     {k: v for k, v in self._device_state_cache[fingerprint].items() 
                      if k not in ['last_updated', 'source']})

    @callback
    def _notify_device_entities(self, fingerprint: str) -> None:
        """通知设备关联的实体更新状态"""
        if fingerprint in self._device_entities:
            for entity_callback in self._device_entities[fingerprint]:
                try:
                    entity_callback()
                except Exception as e:
                    _LOGGER.error("Error notifying entity for device %s: %s", fingerprint, e)

    def register_device_entity(self, fingerprint: str, entity_callback: Callable[[], None]) -> None:
        """注册设备实体回调"""
        if fingerprint not in self._device_entities:
            self._device_entities[fingerprint] = []
        
        if entity_callback not in self._device_entities[fingerprint]:
            self._device_entities[fingerprint].append(entity_callback)
            _LOGGER.debug("Registered entity callback for device %s", fingerprint)

    def unregister_device_entity(self, fingerprint: str, entity_callback: Callable[[], None]) -> None:
        """注销设备实体回调"""
        if fingerprint in self._device_entities and entity_callback in self._device_entities[fingerprint]:
            self._device_entities[fingerprint].remove(entity_callback)
            _LOGGER.debug("Unregistered entity callback for device %s", fingerprint)

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
                # 初始化设备状态
                self._init_device_state(device)
            
            return callback(device, is_new)

        for controller in self._controllers:
            controller.set_device_discovered_callback(wrapped_callback)

    def _init_device_state(self, device: DayBetterDevice) -> None:
        """初始化设备状态"""
        fingerprint = device.fingerprint
        current_time = time.time()
        
        # 初始化状态缓存
        self._device_state_cache[fingerprint] = {
            'on': getattr(device, 'on', False),
            'brightness': getattr(device, 'brightness', 0),
            'rgb_color': getattr(device, 'rgb_color', None),
            'temperature_color': getattr(device, 'temperature_color', None),
            'scene': getattr(device, 'scene', None),
            'last_updated': current_time,
            'source': 'discovery'
        }
        
        # 初始化响应状态
        self._device_last_response[fingerprint] = current_time
        self._device_responded[fingerprint] = True

    def cleanup(self) -> list[asyncio.Event]:
        """Stop and cleanup the coordinator."""
        return [controller.cleanup() for controller in self._controllers]

    async def turn_on(self, device: DayBetterDevice) -> None:
        """Turn on the light."""
        try:
            await device.turn_on()
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {'on': True})
        except Exception as ex:
            _LOGGER.warning("Failed to turn on device %s: %s", device.fingerprint, ex)
            raise

    async def turn_off(self, device: DayBetterDevice) -> None:
        """Turn off the light."""
        try:
            await device.turn_off()
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {'on': False})
        except Exception as ex:
            _LOGGER.warning("Failed to turn off device %s: %s", device.fingerprint, ex)
            raise

    async def set_brightness(self, device: DayBetterDevice, brightness: int) -> None:
        """Set light brightness."""
        try:
            await device.set_brightness(brightness)
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {'brightness': brightness})
        except Exception as ex:
            _LOGGER.warning("Failed to set brightness for device %s: %s", device.fingerprint, ex)
            raise

    async def set_rgb_color(
        self, device: DayBetterDevice, red: int, green: int, blue: int
    ) -> None:
        """Set light RGB color."""
        try:
            await device.set_rgb_color(red, green, blue)
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {
                'rgb_color': (red, green, blue),
                'temperature_color': None  # 切换到RGB模式
            })
        except Exception as ex:
            _LOGGER.warning("Failed to set RGB color for device %s: %s", device.fingerprint, ex)
            raise

    async def set_temperature(self, device: DayBetterDevice, temperature: int) -> None:
        """Set light color in kelvin."""
        try:
            await device.set_temperature(temperature)
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {
                'temperature_color': temperature,
                'rgb_color': None  # 切换到色温模式
            })
        except Exception as ex:
            _LOGGER.warning("Failed to set temperature for device %s: %s", device.fingerprint, ex)
            raise

    async def set_scene(self, device: DayBetterDevice, scene: str) -> None:
        """Set light scene."""
        try:
            await device.set_scene(scene)
            # 更新缓存状态
            self._update_device_state_from_operation(device.fingerprint, {'scene': scene})
        except Exception as ex:
            _LOGGER.warning("Failed to set scene for device %s: %s", device.fingerprint, ex)
            raise

    def _update_device_state_from_operation(self, fingerprint: str, updates: dict) -> None:
        """从操作更新设备状态缓存"""
        if fingerprint in self._device_state_cache:
            self._device_state_cache[fingerprint].update(updates)
            self._device_state_cache[fingerprint]['last_updated'] = time.time()
            self._device_state_cache[fingerprint]['source'] = 'operation'
            
            # 通知实体更新
            self._notify_device_entities(fingerprint)

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
        for fingerprint, last_response in list(self._device_last_response.items()):
            # 如果超过60秒没有响应，认为设备离线
            if current_time - last_response > 60:
                self._device_responded[fingerprint] = False
                _LOGGER.debug("Device %s marked as offline (no response for %d seconds)", 
                             fingerprint, int(current_time - last_response))
            else:
                self._device_responded[fingerprint] = True
        
        return self.devices

    def is_device_responding(self, fingerprint: str) -> bool:
        """检查设备是否响应（在线）"""
        return self._device_responded.get(fingerprint, False)

    def get_cached_device_state(self, fingerprint: str) -> dict:
        """获取设备缓存状态"""
        return self._device_state_cache.get(fingerprint, {})

    def get_device_by_fingerprint(self, fingerprint: str) -> Optional[DayBetterDevice]:
        """根据指纹获取设备对象"""
        for device in self.devices:
            if device.fingerprint == fingerprint:
                return device
        return None