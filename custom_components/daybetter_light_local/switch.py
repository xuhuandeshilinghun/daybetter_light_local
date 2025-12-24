"""DayBetter switch local."""

from __future__ import annotations

import logging
from typing import Any

from daybetter_local_api import DayBetterDevice, DayBetterLightFeatures

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DayBetterLocalApiCoordinator, DayBetterLocalConfigEntry

_LOGGER = logging.getLogger(__name__)


def is_plug_device(device: DayBetterDevice) -> bool:
    """Check if the device is an plug/switch."""
    # 方法1：根据型号判断
    plug_skus = ["P0A1", "P0A2"]  # 假设这些是插座型号，您需要根据实际情况修改
    
    # 方法2：根据能力判断 - 如果不是灯，可能就是插座
    if hasattr(device, 'capabilities') and device.capabilities:
        capabilities = device.capabilities
        # 如果没有灯光能力，可能是插座
        if not (DayBetterLightFeatures.BRIGHTNESS & capabilities.features or
                DayBetterLightFeatures.COLOR_RGB & capabilities.features or
                DayBetterLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features):
            return True
    
    # 方法3：根据型号关键词判断
    plug_keywords = ["plug", "socket", "switch"]
    return any(keyword in device.sku.lower() for keyword in plug_keywords) or device.sku in plug_skus


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DayBetterLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """DayBetter switch setup."""

    coordinator = config_entry.runtime_data

    def discovery_callback(device: DayBetterDevice, is_new: bool) -> bool:
        # 如果是新的插座设备，创建开关实体
        if is_new and is_plug_device(device):
            async_add_entities([DayBetterplugSwitch(coordinator, device)])
        return True

    # 为现有的插座设备创建开关实体
    async_add_entities(
        DayBetterplugSwitch(coordinator, device) 
        for device in coordinator.devices 
        if is_plug_device(device)
    )

    await coordinator.set_discovery_callback(discovery_callback)


class DayBetterplugSwitch(CoordinatorEntity[DayBetterLocalApiCoordinator], SwitchEntity):
    """DayBetter plug/Switch."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_is_on = False  # 默认关闭状态
    _last_known_state = False  # 最后已知状态

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter plug Switch constructor."""
        super().__init__(coordinator)
        self._device = device
        
        # 设置设备更新回调
        original_callback = getattr(device, '_update_callback', None)
        
        def device_update_callback(updated_device: DayBetterDevice):
            # 更新状态
            self._handle_device_update(updated_device)
            # 调用原始回调（如果有）
            if original_callback:
                original_callback(updated_device)
        
        device.set_update_callback(device_update_callback)

        self._attr_unique_id = device.fingerprint + "_plug"
        
        # 为插座创建独立的设备信息
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.fingerprint + "_plug")},  # 使用不同的标识符
            name=f"{device.sku} Plug",
            manufacturer=MANUFACTURER,
            model_id=device.sku,
            serial_number=device.fingerprint,
            via_device=(DOMAIN, device.fingerprint) if hasattr(device, 'parent_device') else None,
        )
        
        _LOGGER.debug("Created plug switch for device: %s (SKU: %s)", 
                     device.fingerprint, device.sku)
        
        # 初始化状态
        self._update_state_from_cache()

    @callback
    def _handle_device_update(self, device: DayBetterDevice) -> None:
        """处理设备更新"""
        if device.fingerprint == self._device.fingerprint:
            self._attr_is_on = getattr(device, 'on', False)
            self._last_known_state = self._attr_is_on
            self._attr_available = True
            self.async_write_ha_state()

    def _update_state_from_cache(self) -> None:
        """从缓存更新状态"""
        cached_state = self.coordinator.get_device_state(self._device.fingerprint)
        if cached_state:
            self._attr_is_on = cached_state.get('on', False)
            self._last_known_state = self._attr_is_on

    @property
    def available(self) -> bool:
        """返回实体是否可用"""
        return self.coordinator.is_device_online(self._device.fingerprint)

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        if self.available:
            # 设备在线，获取实时状态
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                return getattr(device, 'on', False)
        
        # 设备离线，返回缓存状态
        cached_state = self.coordinator.get_device_state(self._device.fingerprint)
        if cached_state:
            return cached_state.get('on', self._last_known_state)
        
        return self._last_known_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self.available:
            await self.coordinator.turn_on(self._device)
            # 更新本地状态
            self._attr_is_on = True
            self._last_known_state = True
        else:
            _LOGGER.warning("Device %s is offline, cannot turn on", self._device.fingerprint)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.available:
            await self.coordinator.turn_off(self._device)
            # 更新本地状态
            self._attr_is_on = False
            self._last_known_state = False
        else:
            _LOGGER.warning("Device %s is offline, cannot turn off", self._device.fingerprint)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """当实体添加到HASS时调用"""
        await super().async_added_to_hass()
        # 监听协调器更新
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器更新"""
        # 更新状态缓存
        self._update_state_from_cache()
        self.async_write_ha_state()