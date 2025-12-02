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


def is_outlet_device(device: DayBetterDevice) -> bool:
    """Check if the device is an outlet/switch."""
    # 方法1：根据型号判断
    outlet_skus = ["P079", "P080", "S001"]  # 假设这些是插座型号，您需要根据实际情况修改
    
    # 方法2：根据能力判断 - 如果不是灯，可能就是插座
    if hasattr(device, 'capabilities') and device.capabilities:
        capabilities = device.capabilities
        # 如果没有灯光能力，可能是插座
        if not (DayBetterLightFeatures.BRIGHTNESS & capabilities.features or
                DayBetterLightFeatures.COLOR_RGB & capabilities.features or
                DayBetterLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features):
            return True
    
    # 方法3：根据型号关键词判断
    outlet_keywords = ["outlet", "plug", "socket", "switch"]
    return any(keyword in device.sku.lower() for keyword in outlet_keywords) or device.sku in outlet_skus


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DayBetterLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """DayBetter switch setup."""

    coordinator = config_entry.runtime_data

    def discovery_callback(device: DayBetterDevice, is_new: bool) -> bool:
        # 如果是新的插座设备，创建开关实体
        if is_new and is_outlet_device(device):
            async_add_entities([DayBetterOutletSwitch(coordinator, device)])
        return True

    # 为现有的插座设备创建开关实体
    async_add_entities(
        DayBetterOutletSwitch(coordinator, device) 
        for device in coordinator.devices 
        if is_outlet_device(device)
    )

    await coordinator.set_discovery_callback(discovery_callback)


class DayBetterOutletSwitch(CoordinatorEntity[DayBetterLocalApiCoordinator], SwitchEntity):
    """DayBetter Outlet/Switch."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter Outlet Switch constructor."""

        super().__init__(coordinator)
        self._device = device
        device.set_update_callback(self._update_callback)

        self._attr_unique_id = device.fingerprint + "_outlet"
        
        # 为插座创建独立的设备信息
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.fingerprint + "_outlet")},  # 使用不同的标识符
            name=f"{device.sku} Outlet",
            manufacturer=MANUFACTURER,
            model_id=device.sku,
            serial_number=device.fingerprint,
            # 可选：添加设备分类为插座
            via_device=(DOMAIN, device.fingerprint) if hasattr(device, 'parent_device') else None,
        )
        
        _LOGGER.debug("Created outlet switch for device: %s (SKU: %s)", 
                     device.fingerprint, device.sku)

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._device.on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.turn_on(self._device)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.turn_off(self._device)
        self.async_write_ha_state()

    @callback
    def _update_callback(self, device: DayBetterDevice) -> None:
        """Push updates from device."""
        self.async_write_ha_state()