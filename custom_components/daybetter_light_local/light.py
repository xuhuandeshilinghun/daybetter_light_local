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

    # 首先为所有已知设备创建实体（包括可能已经离线但已知的设备）
    all_known_devices = coordinator.get_all_known_devices()
    
    # 为当前发现的设备创建实体
    for device in coordinator.devices:
        if is_light_device(device):
            async_add_entities([DayBetterLight(coordinator, device)])
    
    # 为已缓存但当前未发现的设备创建实体
    for fingerprint in all_known_devices:
        cached_state = coordinator.get_cached_device_state(fingerprint)
        if cached_state and 'sku' in cached_state:
            # 检查是否是灯设备
            sku = cached_state.get('sku', '')
            # 简单通过SKU判断是否为灯
            if sku in ["P076", "P077", "P078"]:
                # 创建虚拟设备对象来保存必要信息
                class VirtualDevice:
                    def __init__(self, fingerprint, sku):
                        self.fingerprint = fingerprint
                        self.sku = sku
                        self.on = False
                        self.brightness = 0
                        self.rgb_color = None
                        self.temperature_color = None
                        self.scene = None
                        self.capabilities = None
                
                virtual_device = VirtualDevice(fingerprint, sku)
                async_add_entities([DayBetterLight(coordinator, virtual_device)])
                _LOGGER.info("Created entity for offline light device: %s", fingerprint)

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
    _cached_sku = ""

    def __init__(
        self,
        coordinator: DayBetterLocalApiCoordinator,
        device: DayBetterDevice,
    ) -> None:
        """DayBetter Light constructor."""
        super().__init__(coordinator)
        self._device = device
        self._fingerprint = device.fingerprint
        self._cached_sku = getattr(device, 'sku', 'Unknown')

        self._attr_unique_id = device.fingerprint
        
        # 如果设备有实际对象，设置更新回调
        if hasattr(device, 'set_update_callback'):
            def device_update_callback(updated_device: DayBetterDevice):
                self._handle_device_update(updated_device)
            
            device.set_update_callback(device_update_callback)

        # 初始化设备能力
        self._initialize_capabilities(device)
        
        # 设备信息
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, device.fingerprint)
            },
            name=f"{self._cached_sku} Light",
            manufacturer=MANUFACTURER,
            model_id=self._cached_sku,
            serial_number=device.fingerprint,
        )
        
        # 初始化状态
        self._update_state_from_cache()
        
        _LOGGER.debug("Created light entity for device: %s (SKU: %s)", 
                     device.fingerprint, self._cached_sku)

    def _initialize_capabilities(self, device: DayBetterDevice) -> None:
        """初始化设备能力"""
        pid = ["P076"]
        pid_lower = [p.lower() for p in pid]
        
        color_modes = {ColorMode.ONOFF}
        
        # 尝试从设备获取能力
        if hasattr(device, 'capabilities') and device.capabilities:
            capabilities = device.capabilities
            if DayBetterLightFeatures.COLOR_RGB & capabilities.features:
                color_modes.add(ColorMode.RGB)
            if DayBetterLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features:
                color_modes.add(ColorMode.COLOR_TEMP)
                if self._cached_sku.lower() in pid_lower:
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
        else:
            # 如果没有能力信息，根据SKU猜测
            if self._cached_sku in ["P076", "P077", "P078"]:
                color_modes.add(ColorMode.BRIGHTNESS)
                # P076支持RGB和色温
                if self._cached_sku == "P076":
                    color_modes.add(ColorMode.RGB)
                    color_modes.add(ColorMode.COLOR_TEMP)
                    self._attr_max_color_temp_kelvin = 7000
                    self._attr_min_color_temp_kelvin = 2200
        
        self._attr_supported_color_modes = filter_supported_color_modes(color_modes)
        if len(self._attr_supported_color_modes) == 1:
            self._fixed_color_mode = next(iter(self._attr_supported_color_modes))

    @callback
    def _handle_device_update(self, device: DayBetterDevice) -> None:
        """处理设备更新"""
        if device.fingerprint == self._fingerprint:
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
            self._cached_sku = cached_state.get('sku', self._cached_sku)
            
            if self._cached_scene and self._cached_scene != _NONE_SCENE:
                self._attr_effect = self._cached_scene
            else:
                self._attr_effect = None
            
            _LOGGER.debug("Light %s updated from cache: on=%s, brightness=%s, online=%s", 
                         self._fingerprint, self._cached_on, self._cached_brightness,
                         cached_state.get('online', False))

    @property
    def available(self) -> bool:
        """返回实体是否可用（设备是否在线）"""
        # 实体总是可用的，即使设备离线
        # Home Assistant会在UI中正确显示离线状态
        # 只要实体存在，用户就能看到设备的最后状态
        return self.coordinator.is_device_online(self._fingerprint)

    @property
    def is_on(self) -> bool:
        """Return true if device is on (brightness above 0)."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available and hasattr(self._device, 'on'):
            return getattr(self._device, 'on', self._cached_on)
        
        # 设备离线，使用缓存状态
        return self._cached_on

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available and hasattr(self._device, 'brightness'):
            brightness = getattr(self._device, 'brightness', self._cached_brightness)
            return int((brightness / 100.0) * 255.0)
        
        # 设备离线，使用缓存状态
        return int((self._cached_brightness / 100.0) * 255.0)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available and hasattr(self._device, 'temperature_color'):
            return getattr(self._device, 'temperature_color', self._cached_temperature_color)
        
        # 设备离线，使用缓存状态
        return self._cached_temperature_color

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color."""
        # 如果设备在线，尝试从设备对象获取状态
        if self.available and hasattr(self._device, 'rgb_color'):
            return getattr(self._device, 'rgb_color', self._cached_rgb_color)
        
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
            # 如果设备没有实际对象，需要先获取
            if not hasattr(self._device, 'turn_on'):
                # 尝试从协调器获取实际设备
                real_device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
                if real_device:
                    self._device = real_device
                else:
                    _LOGGER.error("Device %s not found, cannot control", self._fingerprint)
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
            # 如果设备没有实际对象，需要先获取
            if not hasattr(self._device, 'turn_off'):
                # 尝试从协调器获取实际设备
                real_device = self.coordinator.get_device_by_fingerprint(self._fingerprint)
                if real_device:
                    self._device = real_device
                else:
                    _LOGGER.error("Device %s not found, cannot control", self._fingerprint)
                    return
            
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
        self.coordinator.register_device_entity(self._fingerprint, self._handle_state_update)
        
        # 监听协调器定期更新
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        
        # 移除时注销回调
        self.async_on_remove(
            lambda: self.coordinator.unregister_device_entity(self._fingerprint, self._handle_state_update)
        )
    
    @callback
    def _handle_state_update(self) -> None:
        """处理状态更新（协调器调用）"""
        self._update_state_from_cache()
        self.async_write_ha_state()
    
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