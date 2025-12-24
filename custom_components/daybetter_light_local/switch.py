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
    plug_skus = ["P0AB", "P0AC"]  # 假设这些是插座型号，您需要根据实际情况修改
    
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
    _attr_is_on = False

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter plug Switch constructor."""
        super().__init__(coordinator)
        self._device = device
        self._fingerprint = device.fingerprint

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
    def _handle_state_update(self) -> None:
        """处理状态更新"""
        self._update_state_from_cache()
        self.async_write_ha_state()

    def _update_state_from_cache(self) -> None:
        """从缓存更新状态"""
        cached_state = self.coordinator.get_cached_device_state(self._fingerprint)
        if cached_state:
            self._attr_is_on = cached_state.get('on', False)
            _LOGGER.debug("Switch %s updated from cache: %s (online: %s)", 
                         self._fingerprint, self._attr_is_on, 
                         cached_state.get('online', False))

    @property
    def available(self) -> bool:
        """返回实体是否可用（设备是否在线）"""
        return self.coordinator.is_device_online(self._fingerprint)

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
            if device:
                return getattr(device, 'on', self._attr_is_on)
        
        # 设备离线，使用缓存状态
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if not self.available:
            _LOGGER.warning("Cannot turn on device %s - device is offline", self._fingerprint)
            return
            
        try:
            await self.coordinator.turn_on(self._device)
            # 立即更新本地状态
            self._attr_is_on = True
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error("Failed to turn on device %s: %s", self._fingerprint, ex)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if not self.available:
            _LOGGER.warning("Cannot turn off device %s - device is offline", self._fingerprint)
            return
            
        try:
            await self.coordinator.turn_off(self._device)
            # 立即更新本地状态
            self._attr_is_on = False
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error("Failed to turn off device %s: %s", self._fingerprint, ex)

    async def async_added_to_hass(self) -> None:
        """当实体添加到HASS时调用"""
        await super().async_added_to_hass()
        
        # 注册回调到协调器
        self.coordinator.register_entity_callback(self._fingerprint, self._handle_state_update)
        
        # 监听协调器定期更新
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        
        # 移除时注销回调
        self.async_on_remove(
            lambda: self.coordinator.unregister_entity_callback(self._fingerprint, self._handle_state_update)
        )
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器定期更新"""
        self._update_state_from_cache()
        self.async_write_ha_state()