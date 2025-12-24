"""DayBetter light local."""

from __future__ import annotations

import logging
from typing import Any

from daybetter_local_api import DayBetterDevice, DayBetterLightFeatures

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    filter_supported_color_modes,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DayBetterLocalApiCoordinator, DayBetterLocalConfigEntry

_LOGGER = logging.getLogger(__name__)

_NONE_SCENE = "none"


def is_light_device(device: DayBetterDevice) -> bool:
    """Check if the device is a light based on capabilities."""
    if not hasattr(device, 'capabilities') or not device.capabilities:
        return False
    
    capabilities = device.capabilities
    if (DayBetterLightFeatures.BRIGHTNESS & capabilities.features or
        DayBetterLightFeatures.COLOR_RGB & capabilities.features or
        DayBetterLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features):
        return True
    
    light_skus = ["P076", "P077", "P078"]
    return device.sku in light_skus


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DayBetterLocalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """DayBetter light setup."""

    coordinator = config_entry.runtime_data

    def discovery_callback(device: DayBetterDevice, is_new: bool) -> bool:
        if is_new and is_light_device(device):  # 只对灯设备创建实体
            async_add_entities([DayBetterLight(coordinator, device)])
        return True

    # 只添加灯设备
    async_add_entities(
        DayBetterLight(coordinator, device) 
        for device in coordinator.devices 
        if is_light_device(device)
    )

    await coordinator.set_discovery_callback(discovery_callback)


class DayBetterLight(CoordinatorEntity[DayBetterLocalApiCoordinator], LightEntity):
    """DayBetter Light."""

    _attr_translation_key = "daybetter_light"
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes: set[ColorMode]
    _fixed_color_mode: ColorMode | None = None
    _attr_effect_list: list[str] | None = None
    _attr_effect: str | None = None
    _attr_supported_features: LightEntityFeature = LightEntityFeature(0)
    _last_color_state: (
        tuple[
            ColorMode | str | None,
            int | None,
            tuple[int, int, int] | tuple[int | None] | None,
        ]
        | None
    ) = None
    _last_known_on = False
    _last_known_brightness = 0
    _last_known_rgb_color = None
    _last_known_temperature_color = None

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter Light constructor."""
        super().__init__(coordinator)
        self._device = device
        
        # 设置设备更新回调
        original_callback = getattr(device, '_update_callback', None)
        
        def device_update_callback(updated_device: DayBetterDevice):
            self._handle_device_update(updated_device)
            if original_callback:
                original_callback(updated_device)
        
        device.set_update_callback(device_update_callback)

        self._attr_unique_id = device.fingerprint
        pid = ["P076"]
        pid_lower = [p.lower() for p in pid]
        capabilities = device.capabilities
        color_modes = {ColorMode.ONOFF}
        if capabilities:
            if DayBetterLightFeatures.COLOR_RGB & capabilities.features:
                color_modes.add(ColorMode.RGB)
            if DayBetterLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features:
                color_modes.add(ColorMode.COLOR_TEMP)
                if self._device.sku.lower() in pid_lower:
                    self._attr_max_color_temp_kelvin = 7000
                    self._attr_min_color_temp_kelvin = 2200
                else:
                    self._attr_max_color_temp_kelvin = 5000
                    self._attr_min_color_temp_kelvin = 2200
            if DayBetterLightFeatures.BRIGHTNESS & capabilities.features:
                color_modes.add(ColorMode.BRIGHTNESS)

            if (
                DayBetterLightFeatures.SCENES & capabilities.features
                and capabilities.scenes
            ):
                self._attr_supported_features = LightEntityFeature.EFFECT
                self._attr_effect_list = [_NONE_SCENE, *capabilities.scenes.keys()]

        self._attr_supported_color_modes = filter_supported_color_modes(color_modes)
        if len(self._attr_supported_color_modes) == 1:
            self._fixed_color_mode = next(
                iter(self._attr_supported_color_modes)
            )

        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, device.fingerprint)
            },
            name=f"{device.sku} Light",
            manufacturer=MANUFACTURER,
            model_id=device.sku,
            serial_number=device.fingerprint,
        )
        
        # 初始化状态
        self._update_state_from_cache()

    @callback
    def _handle_device_update(self, device: DayBetterDevice) -> None:
        """处理设备更新"""
        if device.fingerprint == self._device.fingerprint:
            self._update_state_from_device(device)
            self._attr_available = True
            self.async_write_ha_state()

    def _update_state_from_device(self, device: DayBetterDevice) -> None:
        """从设备对象更新状态"""
        self._last_known_on = getattr(device, 'on', False)
        self._last_known_brightness = getattr(device, 'brightness', 0)
        self._last_known_rgb_color = getattr(device, 'rgb_color', None)
        self._last_known_temperature_color = getattr(device, 'temperature_color', None)

    def _update_state_from_cache(self) -> None:
        """从缓存更新状态"""
        cached_state = self.coordinator.get_device_state(self._device.fingerprint)
        if cached_state:
            self._last_known_on = cached_state.get('on', False)
            self._last_known_brightness = cached_state.get('brightness', 0)
            self._last_known_rgb_color = cached_state.get('rgb_color', None)
            self._last_known_temperature_color = cached_state.get('temperature_color', None)

    @property
    def available(self) -> bool:
        """返回实体是否可用"""
        return self.coordinator.is_device_online(self._device.fingerprint)

    @property
    def is_on(self) -> bool:
        """Return true if device is on (brightness above 0)."""
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                return getattr(device, 'on', False)
        
        return self._last_known_on

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                brightness = getattr(device, 'brightness', 0)
                return int((brightness / 100.0) * 255.0)
        
        return int((self._last_known_brightness / 100.0) * 255.0)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                return getattr(device, 'temperature_color', None)
        
        return self._last_known_temperature_color

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color."""
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                return getattr(device, 'rgb_color', None)
        
        return self._last_known_rgb_color

    @property
    def color_mode(self) -> ColorMode | str | None:
        """Return the color mode."""
        if self._fixed_color_mode:
            return self._fixed_color_mode

        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._device.fingerprint)
            if device:
                if (
                    getattr(device, 'temperature_color', None) is not None
                    and getattr(device, 'temperature_color', 0) > 0
                ):
                    return ColorMode.COLOR_TEMP
                return ColorMode.RGB
        
        # 离线时根据缓存判断
        if self._last_known_temperature_color is not None and self._last_known_temperature_color > 0:
            return ColorMode.COLOR_TEMP
        return ColorMode.RGB

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        if not self.available:
            _LOGGER.warning("Device %s is offline, cannot turn on", self._device.fingerprint)
            return

        if ATTR_BRIGHTNESS in kwargs:
            brightness: int = int((float(kwargs[ATTR_BRIGHTNESS]) / 255.0) * 100.0)
            await self.coordinator.set_brightness(self._device, brightness)

        if ATTR_RGB_COLOR in kwargs:
            self._attr_color_mode = ColorMode.RGB
            self._attr_effect = None
            self._last_color_state = None
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            await self.coordinator.set_rgb_color(self._device, red, green, blue)
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_effect = None
            self._last_color_state = None
            temperature: float = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self.coordinator.set_temperature(self._device, int(temperature))
        elif ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            if effect and self._attr_effect_list and effect in self._attr_effect_list:
                if effect == _NONE_SCENE:
                    self._attr_effect = None
                    await self._restore_last_color_state()
                else:
                    self._attr_effect = effect
                    self._save_last_color_state()
                    await self.coordinator.set_scene(self._device, effect)

        if not self.is_on or not kwargs:
            await self.coordinator.turn_on(self._device)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        if not self.available:
            _LOGGER.warning("Device %s is offline, cannot turn off", self._device.fingerprint)
            return
            
        await self.coordinator.turn_off(self._device)
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

    def _save_last_color_state(self) -> None:
        color_mode = self.color_mode
        self._last_color_state = (
            color_mode,
            self.brightness,
            (self.color_temp_kelvin,)
            if color_mode == ColorMode.COLOR_TEMP
            else self.rgb_color,
        )

    async def _restore_last_color_state(self) -> None:
        if self._last_color_state:
            color_mode, brightness, color = self._last_color_state
            if color:
                if color_mode == ColorMode.RGB:
                    await self.coordinator.set_rgb_color(self._device, *color)
                elif color_mode == ColorMode.COLOR_TEMP:
                    await self.coordinator.set_temperature(self._device, *color)
            if brightness:
                await self.coordinator.set_brightness(
                    self._device, int((float(brightness) / 255.0) * 100.0)
                )
            self._last_color_state = None