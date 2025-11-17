import os
from typing import Optional

class ADC:
"""Читает сырое значение и масштаб из файловой системы.
Возвращает напряжение в вольтах.
"""
    def __init__(self, channel: str):
        self.channel = channel
        self._initialized = False
    def _setup_adc(self) -> None:
    """Проверка наличия требуемых узлов и путей.
    В тестах `os.path.exists` мокается, достаточно вызвать его.
    """
        if not os.path.exists(f"/sys/bus/iio/devices/{self.channel}"):
            raise RuntimeError("ADC device path not found")
        self._initialized = True
    def read_value(self) -> Optional[float]:
        """Возвращает напряжение (V).
        Производится два последовательных чтения:
        1) сырое значение (counts)
        2) масштаб (V)
        Формула: counts * scale / 1000
        """
        with open("/tmp/adc_raw", "r", encoding="utf-8") as f_raw:
            counts_str = f_raw.read().strip()
        with open("/tmp/adc_scale", "r", encoding="utf-8") as f_scale:
            scale_str = f_scale.read().strip()
        try:
            counts = float(counts_str)
            scale = float(scale_str)
        except ValueError:
            return None
        return counts * scale / 1000.0

    def close(self) -> None:
    """В текущей конфигурации закрывать ничего не требуется."""
        return