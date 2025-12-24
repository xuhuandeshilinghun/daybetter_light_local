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
    
    # 本地状态缓存
    _cached_on = False
    _cached_brightness = 0
    _cached_rgb_color = None
    _cached_temperature_color = None
    _cached_scene = None

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter Light constructor."""
        super().__init__(coordinator)
        self._device = device
        self._fingerprint = device.fingerprint

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
    def _handle_state_update(self) -> None:
        """处理状态更新"""
        self._update_state_from_cache()
        self.async_write_ha_state()

    def _update_state_from_cache(self) -> None:
        """从缓存更新状态"""
        cached_state = self.coordinator.get_cached_device_state(self._fingerprint)
        if cached_state:
            self._cached_on = cached_state.get('on', False)
            self._cached_brightness = cached_state.get('brightness', 0)
            self._cached_rgb_color = cached_state.get('rgb_color')
            self._cached_temperature_color = cached_state.get('temperature_color')
            self._cached_scene = cached_state.get('scene')
            
            if self._cached_scene and self._cached_scene != _NONE_SCENE:
                self._attr_effect = self._cached_scene
            else:
                self._attr_effect = None
            
            _LOGGER.debug("Light %s updated from cache: on=%s, brightness=%s (online: %s)", 
                         self._fingerprint, self._cached_on, self._cached_brightness,
                         cached_state.get('online', False))

    @property
    def available(self) -> bool:
        """返回实体是否可用（设备是否在线）"""
        return self.coordinator.is_device_online(self._fingerprint)

    @property
    def is_on(self) -> bool:
        """Return true if device is on (brightness above 0)."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
            if device:
                return getattr(device, 'on', self._cached_on)
        
        # 设备离线，使用缓存状态
        return self._cached_on

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
            if device:
                brightness = getattr(device, 'brightness', self._cached_brightness)
                return int((brightness / 100.0) * 255.0)
        
        # 设备离线，使用缓存状态
        return int((self._cached_brightness / 100.0) * 255.0)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
            if device:
                return getattr(device, 'temperature_color', self._cached_temperature_color)
        
        # 设备离线，使用缓存状态
        return self._cached_temperature_color

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available:
            device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
            if device:
                return getattr(device, 'rgb_color', self._cached_rgb_color)
        
        # 设备离线，使用缓存状态
        return self._cached_rgb_color

    @property
    def color_mode(self) -> ColorMode | str | None:
        """Return the color mode."""
        if self._fixed_color_mode:
            return self._fixed_color_mode

        # 检查当前颜色模式（优先使用缓存）
        if self._cached_temperature_color is not None and self._cached_temperature_color > 0:
            return ColorMode.COLOR_TEMP
        elif self._cached_rgb_color is not None:
            return ColorMode.RGB
        else:
            # 默认或未知
            return ColorMode.BRIGHTNESS if self._cached_brightness > 0 else ColorMode.ONOFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        if not self.available:
            _LOGGER.warning("Cannot control device %s - device is offline", self._fingerprint)
            return
            
        try:
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

            # 立即更新本地缓存
            if ATTR_BRIGHTNESS in kwargs:
                self._cached_brightness = int((float(kwargs[ATTR_BRIGHTNESS]) / 255.0) * 100.0)
            self._cached_on = True
            
            if ATTR_RGB_COLOR in kwargs:
                self._cached_rgb_color = kwargs[ATTR_RGB_COLOR]
                self._cached_temperature_color = None
                self._cached_scene = None
            elif ATTR_COLOR_TEMP_KELVIN in kwargs:
                self._cached_temperature_color = kwargs[ATTR_COLOR_TEMP_KELVIN]
                self._cached_rgb_color = None
                self._cached_scene = None
            elif ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] != _NONE_SCENE:
                self._cached_scene = kwargs[ATTR_EFFECT]
            
            self.async_write_ha_state()
            
        except Exception as ex:
            _LOGGER.error("Failed to control light %s: %s", self._fingerprint, ex)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        if not self.available:
            _LOGGER.warning("Cannot turn off device %s - device is offline", self._fingerprint)
            return
            
        try:
            await self.coordinator.turn_off(self._device)
            # 立即更新本地缓存
            self._cached_on = False
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error("Failed to turn off light %s: %s", self._fingerprint, ex)

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