import serial, asyncio, binascii, math
from typing import Dict, Any, Optional, Tuple
import logging, time, threading

logger = logging.getLogger(__name__)

class Inclinometer:
    """Класс для работы с инклинометром по UART с использованием async I/O в executor."""

    DEFAULT_TIMEOUT_SEC = 0.2

    def __init__(self, device: str, baudrate: int = 115200, message_header_hex: str = "7710FF84", timeout: float = DEFAULT_TIMEOUT_SEC):
        self.logger = logging.getLogger(__name__)
        self.device = device
        self.baudrate = baudrate
        # Устанавливаем таймаут для синхронных операций serial
        # Важно: этот таймаут влияет на read(), readuntil()
        self.timeout = timeout 
        self.serial_port: Optional[serial.Serial] = None
        # Блокировка для синхронизации доступа к порту
        self._lock = threading.Lock() 
        self._is_connected = False

        try:
            self.message_header = bytes.fromhex(message_header_hex.lower())
        except ValueError:
            self.logger.exception(f"Неверный формат HEX заголовка: {message_header_hex}")
            raise ValueError("Неверный формат HEX заголовка")
        
        self.header_len = len(self.message_header)
        # два BCD-значения по 4 байта каждое
        self.data_len = 8 
        self.crc_len = 1
        self.full_message_length = self.header_len + self.data_len + self.crc_len
        if self.full_message_length <= self.header_len:
            raise ValueError("Длина полного сообщения должна быть больше длины заголовка")

        # Длина заголовка (4 байта)
        self.header_len = len(self.message_header)

        # Предварительно вычисляем длины
        self.header_len_bytes = len(self.message_header)

        self.logger.info(f"Inclinometer создан: device={device}, baudrate={baudrate}, header={message_header_hex}, timeout={self.timeout}s")

    # Метод connect может оставаться async, если вызывается из async контекста,
    # но сама операция открытия порта синхронная.
    async def connect(self): 
        """Устанавливает соединение с UART устройством (операция синхронная)."""
        # Используем threading.Lock для синхронизации
        with self._lock: 
            if self._is_connected and self.serial_port and self.serial_port.is_open:
                self.logger.debug(f"Уже подключено к {self.device}")
                return True
            try:
                self.logger.info(f"Подключение к {self.device}...")
                # Закрываем старое соединение, если оно есть
                if self.serial_port and self.serial_port.is_open:
                    self.serial_port.close()
                    
                # Создаем и открываем порт синхронно
                self.serial_port = serial.Serial(
                    port=self.device,
                    baudrate=self.baudrate,
                    # Таймаут для read операций
                    timeout=self.timeout 
                )
                # Проверка, открылся ли порт
                if self.serial_port.is_open:
                    self._is_connected = True
                    # Очищаем буфер при подключении
                    self.serial_port.reset_input_buffer() 
                    self.logger.info(f"Успешно подключено к {self.device}")
                    return True
                else:
                    self.logger.error(f"Не удалось открыть порт {self.device}, is_open=False")
                    self._is_connected = False
                    self.serial_port = None
                    return False
            except serial.SerialException as e:
                self.logger.error(f"Ошибка serial при подключении к {self.device}: {e}")
                self._is_connected = False
                self.serial_port = None
                return False
            except Exception as e:
                self.logger.exception(f"Неожиданная ошибка при подключении к {self.device}")
                self._is_connected = False
                self.serial_port = None
                return False

    def is_connected(self) -> bool:
        """Возвращает статус подключения."""
        with self._lock:
            return self._is_connected and self.serial_port is not None and self.serial_port.is_open

    # Метод close может оставаться async, если вызывается из async контекста,
    # но сама операция закрытия порта синхронная.
    async def close(self):
        """Закрывает соединение (операция синхронная)."""
        with self._lock:
            if not self._is_connected or not self.serial_port:
                self.logger.debug(f"Соединение с {self.device} уже было закрыто или не установлено.")
                return
            try:
                self.logger.info(f"Закрытие соединения с {self.device}...")
                if self.serial_port and self.serial_port.is_open:
                    self.serial_port.close()
                self._is_connected = False
                # Сбрасываем объект порта
                self.serial_port = None 
                self.logger.info(f"Соединение с {self.device} закрыто.")
            except Exception as e:
                self.logger.exception(f"Ошибка при закрытии соединения с {self.device}")
                # Все равно сбрасываем флаги и объект
                self._is_connected = False
                self.serial_port = None

    # --- Асинхронный метод-  обертка --- 
    async def read_data_async(self) -> Optional[Dict[str, float]]:
        """Асинхронно читает и разбирает сообщение от инклинометра.
        
        Returns:
            Optional[Dict[str, float]]: Словарь с углами наклона или None при ошибке
        """
        if not self._is_connected or not self.serial_port:
            try:
                if not await self.connect():
                    self.logger.error("Failed to reconnect to inclinometer")
                    return None
            except Exception as e:
                self.logger.error(f"Error reconnecting to inclinometer: {e}")
                return None

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._sync_read_parse_message)
            if result is None:
                self.logger.warning("Failed to read valid data from inclinometer")
            return result
        except serial.SerialException as e:
            self.logger.error(f"Serial communication error: {e}")
            try:
                # Закрываем порт при ошибке
                await self.close()  
            except Exception:
                pass
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error reading inclinometer data: {e}")
            return None

    # --- Синхронная функция для выполнения в executor --- 
    def _sync_read_parse_message(self) -> Optional[Dict[str, float]]:
        """Синхронное чтение и разбор сообщения.
        
        Returns:
            Dict с углами наклона или None в случае ошибки
        """
        if not self._is_connected or not self.serial_port:
            logger.warning("Порт закрыт")
            return None
            
        try:
            with self._lock:
                # Ищем заголовок
                header = bytes()
                while len(header) < len(self.message_header):
                    # Явно указываем размер чтения
                    byte = self.serial_port.read(1)  
                    if not byte:
                        logger.warning("Таймаут при чтении заголовка")
                        return None
                    header += byte
                    # Если накопленный заголовок не совпадает с началом message_header,
                    # отбрасываем первый байт и продолжаем поиск
                    if not self.message_header.startswith(header):
                        header = header[1:] if len(header) > 1 else bytes()
                
                if header != self.message_header:
                    logger.warning("Неверный заголовок сообщения")
                    return None
                
                # Читаем данные (8 байт)
                payload = bytes()
                for _ in range(8):
                    byte = self.serial_port.read(1)
                    if not byte:
                        logger.warning("Таймаут при чтении данных")
                        return None
                    payload += byte
                    
                # Пропускаем байт контрольной суммы
                self.serial_port.read(1)
                    
                # Разбираем данные
                try:
                    angle1 = float(self._bcd_decode(payload[0:4]))
                    angle2 = float(self._bcd_decode(payload[4:8]))
                except ValueError as e:
                    logger.warning(f"Ошибка декодирования BCD: {e}")
                    return None
                
                return {
                    'angle_roll': angle1,
                    'angle_pitch': angle2
                }
                
        except serial.SerialTimeoutException:
            logger.warning("Таймаут при чтении данных")
            return None
        except Exception as e:
            logger.error(f"Ошибка при чтении данных: {e}")
            return None

    def _parse_message(self, message: bytes) -> Dict[str, Any]:
        angle_roll_raw = int.from_bytes(message[4:6], byteorder='big', signed=True)
        angle_pitch_raw = int.from_bytes(message[6:8], byteorder='big', signed=True)
        elevation_raw = int.from_bytes(message[8:9], byteorder='big', signed=True)
        slope_raw = int.from_bytes(message[9:10], byteorder='big', signed=False)
        angle_roll = angle_roll_raw * 0.01
        angle_pitch = angle_pitch_raw * 0.01
        elevation = elevation_raw * 0.5
        slope = slope_raw * 0.1
        data = {
            'angle_roll': angle_roll,
            'angle_pitch': angle_pitch,
            'elevation': elevation,
            'slope': slope,
        }
        return data

    def _bcd_decode(self, oBytes: bytes) -> str:
        """Декодирует 4- байтовое BCD- значение в строку с плавающей точкой."""
        # первый байт: знак в BCD 0x10 означает отрицательное
        sign = '-' if binascii.hexlify(oBytes[0:1]).decode() == '10' else ''
        int_part = binascii.hexlify(oBytes[1:2]).decode()
        frac1 = binascii.hexlify(oBytes[2:3]).decode()
        frac2 = binascii.hexlify(oBytes[3:4]).decode()
        return f"{sign}{int_part}.{frac1}{frac2}"

    # Синхронный метод для тестов и legacy API
    def _setup_port(self):
        """Инициализация serial- порта через pyserial."""
        try:
            self.serial_port = serial.Serial(
                port=self.device,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
        except serial.SerialException as e:
            raise RuntimeError(f"Не удалось открыть порт {self.device}: {e}")
