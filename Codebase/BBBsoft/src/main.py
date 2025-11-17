# -*- coding: utf-8 -*-
#!/usr/bin/env python
import os, sys, argparse, logging, time, asyncio
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
import zmq.asyncio
import math, random, statistics
import yaml, importlib.metadata
from collections import deque
from typing import Dict, Any, Optional, Deque, Tuple, TYPE_CHECKING

from src.sensors.inclinometer import Inclinometer
from src.communication.zmq_handler import ZMQHandler

try:
    from src import _build_info
except ImportError:
    class _BuildInfoMock:
        VERSION = "0.0.0-unknown-mock" # Отличаем от PackageNotFound
        COMMIT_HASH = "unknown"
        COMMIT_DATE = "unknown"
    _build_info = _BuildInfoMock()
    print("ПРЕДУПРЕЖДЕНИЕ: Не удалось загрузить информацию о сборке (src/_build_info.py). Отображается базовая версия.", file=sys. stderr)

async def measure_inclinometer_frequency(config: Dict[str, Any], duration_sec: int = 2) -> Optional[float]:
    """Измеряет фактическую частоту поступления данных с инклинометра."""
    logger = logging.getLogger("MeasureInclinometer")
    inclinometer = None
    try:
        inc_config = config['sensors']['inclinometer']
        device = inc_config.get('device')
        baudrate = inc_config.get('baudrate', 115200)
        message_header_hex = inc_config.get('message_header_hex')
        if not device or not message_header_hex:
            logger.error("Недостаточно параметров конфигурации для измерения частоты инклинометра.")
            return None

        logger.info(f"Начинаем измерение частоты инклинометра на {device} в течение {duration_sec} сек.")
        inclinometer = Inclinometer(device=device, baudrate=baudrate, message_header_hex=message_header_hex)
        connected = await inclinometer.connect()
        if not connected:
            logger.error("Не удалось подключиться к инклинометру для измерения частоты.")
            return None
            
        logger.debug("Measure Freq: Connection successful, entering read loop.")

        count = 0
        start_time = time.monotonic()
        end_time = start_time + duration_sec
        last_log_time = start_time

        while time.monotonic() < end_time:
            current_loop_time = time.monotonic()
            if current_loop_time - last_log_time > 1.0: 
                logger.debug(f"Measure Freq Loop: Time left {end_time - current_loop_time:.1f}s, Count={count}")
                last_log_time = current_loop_time

            try:
                logger.debug(f"Measure Freq Loop: Calling read_data_async...")
                read_start = time.monotonic()
                data = await inclinometer.read_data_async()
                read_duration = time.monotonic() - read_start
                logger.debug(f"Measure Freq Loop: read_data_async finished in {read_duration:.4f}s. Data received: {bool(data)}")

                if data:
                    count += 1
                else:
                    # Небольшая пауза, если данных нет: 1 мс
                    await asyncio.sleep(0.001)
            except Exception as e:
                logger.error(f"Ошибка при чтении инклинометра во время измерения частоты: {e}")
                # При серьезной ошибке прерываем измерение
                if not inclinometer.is_connected():
                    logger.error("Потеряно соединение во время измерения.")
                    break
                await asyncio.sleep(0.1)

        actual_duration = time.monotonic() - start_time
        # Избегаем деления на ноль, если измерение прервалось мгновенно
        frequency = count / actual_duration if actual_duration > 0.001 else 0
        logger.info(f"Измерение завершено. Получено {count} сообщений за {actual_duration:.2f} сек. Расчетная частота: {frequency:.2f} Гц")
        return frequency

    except Exception as e:
        logger.exception("Ошибка во время измерения частоты инклинометра")
        return None
    finally:
        if inclinometer:
            try:
                await inclinometer.close()
                logger.info("Ресурс Inclinometer для измерения частоты освобожден.")
            except Exception as e:
                logger.error(f"Ошибка при закрытии Inclinometer: {e}")

class MeasurementSystem:
    adc: Optional['ADC']
    inclinometer: Optional[Inclinometer]    # Inclinometer импортирован
    zmq_handler: Optional[ZMQHandler]       # ZMQHandler импортирован

    def __init__(self, config: Dict[str, Any], simulate: bool = False):
        """Инициализация асинхронной системы измерений."""
        self.config = config
        self.simulate = simulate
        self._setup_logging()
        self.logger.info(f"Асинхронная система измерений инициализируется... (Режим симуляции: {'ВКЛ' if simulate else 'ВЫКЛ'})")

        # Инициализация RCpy (важно сделать до ADC), только если не симуляция
        if not self.simulate:
            logger.debug("Before first _init_rcpy call in __init__")
            self._init_rcpy()
            logger.debug("After first _init_rcpy call in __init__")
        else:
            self.logger.info("Режим симуляции: Пропуск инициализации RCpy.")

        # --- Инициализация компонентов (синхронная часть) ---
        if self.simulate:
            self.logger.info("Режим симуляции: Пропуск инициализации реальных сенсоров.")
            self.adc = None
            self.inclinometer = None
        else:
            # Инициализируем реальные сенсоры только если не симуляция
            self.adc = self._init_adc()
            self.inclinometer = self._init_inclinometer()

        # Инициализируем ZMQ всегда
        self.zmq_handler = self._init_zmq()

        # Параметры обработки
        # Получаем значения из конфига и преобразуем в float
        try:
            processing_freq_str = str(self.config.get('processing', {}).get('frequency_hz', '1.0')).replace(',', '.')
            self.processing_freq_hz = float(processing_freq_str)
        except ValueError:
            self.logger.warning(f"Не удалось преобразовать processing frequency '{processing_freq_str}' в float. Используется 1.0.")
            self.processing_freq_hz = 1.0

        try:
            aggregation_period_str = str(self.config.get('processing', {}).get('aggregation_period_sec', '1.0')).replace(',', '.')
            self.aggregation_period_sec = float(aggregation_period_str)
        except ValueError:
            self.logger.warning(f"Не удалось преобразовать aggregation period '{aggregation_period_str}' в float. Используется 1.0.")
            self.aggregation_period_sec = 1.0

        if self.processing_freq_hz <= 0: self.processing_freq_hz = 1.0
        if self.aggregation_period_sec <= 0: self.aggregation_period_sec = 1.0
        self.processing_interval_sec = 1.0 / self.processing_freq_hz

        # Определение размеров очередей
        # Получаем частоту ADC и преобразуем в float
        try:
            adc_freq_str = str(self.config.get('sensors', {}).get('adc', {}).get('frequency_hz', '10.0')).replace(',', '.')
            self.adc_freq_hz = float(adc_freq_str)
        except ValueError:
            self.logger.warning(f"Не удалось преобразовать ADC frequency '{adc_freq_str}' в float. Используется 10.0.")
            self.adc_freq_hz = 10.0

        if self.adc_freq_hz <= 0: self.adc_freq_hz = 10.0
        self.adc_interval_sec = 1.0 / self.adc_freq_hz
        self.adc_queue_size = max(1, int(self.adc_freq_hz * self.aggregation_period_sec))

        # Частоту инклинометра нужно измерить ДО создания очереди
        self.inclinometer_freq_hz = 5
        self.inclinometer_queue_size = max(1, int(self.inclinometer_freq_hz * self.aggregation_period_sec))

        # Очереди для данных (timestamp, value)
        # Для ADC храним raw значения
        self.adc_data_queue: 
            Deque[Tuple[float, int]] = deque(maxlen=self.adc_queue_size)
        # Для Inclinometer храним полные словари
        self.inclinometer_data_queue: 
            Deque[Tuple[float, Dict[str, Any]]] = deque(maxlen=self.inclinometer_queue_size)

        self._tasks = [] # Список для хранения запущенных задач asyncio
        self._shutdown_event = asyncio.Event()

        self.logger.info(f"ADC: частота={self.adc_freq_hz}Гц, очередь={self.adc_queue_size} ({self.aggregation_period_sec}с)")
        self.logger.info("Инициализация завершена (синхронная часть)")

    def _init_rcpy(self):
        """Инициализация RCpy. Вызывается только если НЕ симуляция."""
        self.logger.debug("Вход в _init_rcpy")
        global rcpy # Используем глобальные переменные rcpy, rcpy_adc
        global rcpy_adc
        global rcpy_available # и флаг доступности

        # Импортируем rcpy
        try:
            self.logger.debug("Попытка импорта rcpy...")
            import rcpy
            import rcpy.adc as rcpy_adc
            rcpy_available = True
            self.logger.debug("rcpy и rcpy.adc импортированы успешно.")
        except ImportError as e:
            self.logger.debug(f"Ошибка импорта rcpy или rcpy.adc: {e}")
            self.logger.error("Модуль rcpy или rcpy.adc не найден! Установите его для работы с оборудованием.")
            rcpy_available = False
            # Не выбрасываем исключение здесь, 
            # чтобы позволить работать в режиме симуляции.
            # Но ADC работать не будет.
            self.logger.debug("Выход из _init_rcpy (rcpy недоступен)")
            return # Выходим, если импорт не удался

        # --- Дальнейшая инициализация только если импорт успешен ---
        try:
            self.logger.debug("Получение текущего состояния rcpy...")
            current_state = rcpy.get_state()
            self.logger.debug(f"Текущее состояние rcpy: {current_state}")

            if current_state == rcpy.EXITING:
                self.logger.debug("Состояние EXITING, вызов rcpy.initialize()...")
                rcpy.initialize()
                self.logger.info("RCpy инициализирован.")
            elif current_state == rcpy.RUNNING:
                self.logger.info("RCpy уже инициализирован.")
            else:
                self.logger.warning(f"RCpy в состоянии {current_state}. Попытка запуска...")
                rcpy.run()
                self.logger.debug("rcpy.run() завершен.")
                # Повторно проверяем состояние после вызова run()
                new_state = rcpy.get_state()
                self.logger.debug(f"Новое состояние rcpy после run(): {new_state}")
                if new_state != rcpy.RUNNING:
                    self.logger.debug(f"Ошибка: не удалось перевести в RUNNING.")
                    raise RuntimeError(f"Не удалось перевести RCpy в состояние RUNNING (текущее: {new_state})")
                self.logger.info(f"RCpy переведен в состояние RUNNING.")
        except Exception as e:
            self.logger.debug(f"Исключение в _init_rcpy: {e}")
            self.logger.exception("Критическая ошибка инициализации RCpy")
            raise RuntimeError(f"Не удалось инициализировать RCpy: {e}")
        finally:
            logger.debug("Выход из _init_rcpy")

    def _init_adc(self) -> Optional['ADC']:
        """Инициализация датчика ADC. Вызывается только если НЕ симуляция.
           Больше не создает объект ADC, так как используется прямой вызов 
           функций. Возвращает None."""
        if not self.simulate and rcpy_available:
             try:
                  channel = self.config['sensors']['adc']['channel']
                  self.logger.info(f"Конфигурация ADC для канала {channel} найдена.")
                  _ = rcpy.adc.get_raw(channel) # Пробное чтение
                  self.logger.info(f"Пробное чтение rcpy.adc.get_raw({channel}) успешно.")
             except KeyError as e:
                  self.logger.error(f"Отсутствует ключ в конфигурации ADC: {e}. ADC не будет использоваться.")
             except AttributeError:
                  self.logger.error("Ошибка доступа к rcpy.adc функциям. Проверьте установку rcpy.")
             except Exception as e:
                  self.logger.exception(f"Неожиданная ошибка при проверке ADC канала {channel}: {e}")
        elif not self.simulate:
             self.logger.warning("rcpy недоступен, ADC инициализация пропускается.")
        return None # Возвращаем None, так как объекта ADC больше нет

    def _init_inclinometer(self) -> Optional[Inclinometer]:
        """Инициализация датчика Inclinometer. 
           Вызывается только если НЕ симуляция."""
        # --- Логика для реального Inclinometer ---
        try:
            inc_config = self.config['sensors']['inclinometer']
            device = inc_config.get('device')
            baudrate = inc_config.get('baudrate', 115200)
            message_header_hex = inc_config.get('message_header_hex')
            if not device or not message_header_hex:
                raise ValueError("Не указаны device или message_header_hex для инклинометра")

            inclinometer = Inclinometer(device=device, baudrate=baudrate, 
                                      message_header_hex=message_header_hex)
            self.logger.info(f"Объект Inclinometer создан: {device}@{baudrate}")
            return inclinometer
        except KeyError as e:
            self.logger.error(f"Отсутствует ключ в конфигурации Inclinometer: {e}")
            return None
        except Exception as e:
            self.logger.exception("Ошибка создания объекта Inclinometer")
            return None

    def _init_zmq(self) -> Optional[ZMQHandler]:
        """Инициализация ZMQHandler."""
        try:
            zmq_config = self.config['communication']['zmq']
            port = zmq_config.get('port', 5555)
            identity = zmq_config.get('identity', 'BBBlue')
            socket_type = zmq_config.get('socket_type', 'DEALER')

            # Определяем хост в зависимости от режима симуляции
            if self.simulate:
                target_host = zmq_config.get('sim_host', 'localhost')
                self.logger.info(f"Режим симуляции: ZMQ будет подключаться к sim_host: {target_host}")
            else:
                target_host = zmq_config.get('host', 'localhost')
            
            # Используем асинхронный ZMQHandler
            handler = ZMQHandler(
                host=target_host,
                port=port,
                identity=identity,
                socket_type=socket_type,
            )
            return handler
        except KeyError as e:
            self.logger.error(f"Отсутствует ключ в конфигурации ZMQ: {e}")
            return None
        except Exception as e:
            self.logger.exception("Ошибка создания объекта ZMQHandler")
            return None

    def _setup_logging(self):
        """Настройка логирования с использованием RichHandler."""
        try:
            log_config = self.config.get('logging', {'level': 'INFO', 'file': './bbbsoft.log'})
            log_file = log_config.get('file', './bbbsoft.log')
            log_level_str = log_config.get('level', 'INFO').upper()
            log_level = getattr(logging, log_level_str, logging.INFO)
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Форматтер для файла и консоли
            log_formatter = logging.Formatter("%(asctime)s - %(name)s:%(levelname)s - %(message)s")

            # Обработчик для файла
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=log_config.get('maxBytes', 10*1024*1024), # 10 MB
                backupCount=log_config.get('backupCount', 5)
            )
            file_handler.setFormatter(log_formatter)

            handler = logging.StreamHandler()
            handler.setFormatter(log_formatter)
            # Отдельный уровень для консоли
            console_log_level_str = log_config.get('console_level', log_level_str).upper()
            console_log_level = getattr(logging, console_log_level_str, log_level)
            handler.setLevel(console_log_level)

            # Настраиваем базовую конфигурацию
            logging.basicConfig(
                level=log_level,
                # Формат по умолчанию для других логгеров
                format='%(asctime)s - %(name)s:%(levelname)s - %(message)s',
                handlers=[
                    file_handler,
                    handler
                ],
                force=True
            )

            self.logger = logging.getLogger('BBBsoft')
            # Устанавливаем уровень для нашего логгера явно
            self.logger.setLevel(log_level)

            self.logger.info(f'Логирование настроено: Уровень={log_level_str}, Консоль={console_log_level_str}, Файл="{log_file}"')
        except Exception as e:
            # Используем print для вывода, если логгер недоступен
            print(f"CRITICAL: Ошибка настройки логирования: {e}", file=__import__('sys').stderr)
            # Настраиваем простое логирование в stderr как fallback
            logging.basicConfig(level=logging.WARNING, 
                                format='%(levelname)s: %(message)s', 
                                force=True, 
                                handlers=[logging.StreamHandler()])
            self.logger = logging.getLogger('BBBsoft_fallback')
            self.logger.warning("Используется аварийный режим логирования.")

    async def _adc_reader_task(self):
        """Асинхронная задача для чтения ADC или симуляции данных."""
        task_name = "ADCReaderTask"
        if self.simulate:
            self.logger.info(f"[{task_name}] Запуск задачи генерации симулированных данных ADC...")
        else:
            self.logger.info(f"[{task_name}] Запуск задачи чтения ADC...")
            if not rcpy_available:
                self.logger.error(f"[{task_name}] RCpy или его модуль ADC недоступны, задача чтения не будет запущена.")
                return

        simulation_start_time = time.time()

        while not self._shutdown_event.is_set():
            self.logger.debug(f"[{task_name}] Entering main loop iteration.")
            try:
                # --- Обертка для основного блока --- 
                loop = asyncio.get_event_loop()
                start_time = loop.time()
                try:
                    timestamp = time.time() # Общий timestamp

                    if self.simulate:
                        # --- Логика симуляции ADC ---
                        sim_time = timestamp - simulation_start_time
                        base_value = 2047.5 * (1 + math.sin(2 * math.pi * sim_time / 20.0))
                        noise = random.uniform(-50, 50)
                        simulated_value = int(max(0, min(4095, base_value + noise)))
                        self.adc_data_queue.append((timestamp, simulated_value))
                    elif rcpy_available: # Используем флаг доступности rcpy
                        # --- Логика чтения реального ADC --- 
                        try:
                             current_state = rcpy.get_state()
                             if current_state != rcpy.RUNNING:
                                self.logger.warning(f"[{task_name}] Попытка чтения ADC, но RCpy не в состоянии RUNNING ({current_state}). Пропуск чтения.")
                             else:
                                  # Используем прямой вызов rcpy.adc
                                  # Получаем канал из конфига
                                  channel = self.config['sensors']['adc']['channel']
                                  # Синхронная функция, но быстрая
                                  raw_value = rcpy_adc.get_raw(channel) 
                                  self.adc_data_queue.append((timestamp, raw_value))
                                  self.logger.debug(f"[{task_name}] ADC read: {raw_value}")
                        except KeyError: # Если канал не задан в конфиге
                             self.logger.error(f"[{task_name}] Канал ADC не найден в конфигурации.")
                             await asyncio.sleep(5.0) # Пауза от спама в лог
                        except AttributeError as e:
                             self.logger.error(f"[{task_name}] Ошибка доступа к rcpy.adc.get_raw: {e}. Проверьте установку rcpy.")
                             await asyncio.sleep(5.0) 
                        except Exception as e: # Ловим другие ошибки rcpy
                             self.logger.exception(f"[{task_name}] Ошибка при чтении raw ADC: {e}")
                             await asyncio.sleep(1.0)
                    else:
                        # rcpy недоступен, ничего не читаем
                        pass # Не добавляем ничего в очередь

                except Exception as e:
                    # Этот блок ловит ошибки внутри основной логики цикла
                    log_func = self.logger.exception if not self.simulate 
                                                      else self.logger.error
                    log_func(f"[{task_name}] Ошибка в основной логике цикла {'чтения' if not self.simulate else 'симуляции'} ADC: {e}")
                    await asyncio.sleep(1.0) # Добавим паузу и здесь
                finally:
                    # Расчет времени ожидания для поддержания частоты 
                    # (одинаков для обоих режимов)
                    # Вынесем в finally, чтобы выполнялось даже при ошибке
                    await self._wait_for_next_cycle(start_time, self.adc_interval_sec, task_name)
            
            except Exception as e:
                # --- Внешняя обертка для ловли ВСЕХ исключений в цикле --- 
                self.logger.exception(f"[{task_name}] КРИТИЧЕСКАЯ НЕОБРАБОТАННАЯ ОШИБКА в главном цикле задачи! {e}")
                # Долгая пауза, чтобы предотвратить быстрое повторение ошибки
                await asyncio.sleep(5.0)

        self.logger.info(f"[{task_name}] Задача завершена.")

    async def _inclinometer_reader_task(self):
        """Асинхронная задача для чтения Inclinometer 
           или генерации симулированных данных."""
        task_name = "InclinometerReaderTask"
        if self.simulate:
            self.logger.info(f"[{task_name}] Запуск задачи генерации симулированных данных Inclinometer...")
        else:
            self.logger.info(f"[{task_name}] Запуск задачи чтения Inclinometer...")
            if not self.inclinometer:
                self.logger.error(f"[{task_name}] Inclinometer не инициализирован, задача чтения не будет запущена.")
                return

        simulation_start_time = time.time()
        inclinometer_interval_sec = 1.0 / self.inclinometer_freq_hz 

        while not self._shutdown_event.is_set():
            self.logger.debug(f"[{task_name}] Entering main loop iteration.")
            try:
                loop = asyncio.get_event_loop()
                start_time = loop.time()
                try:
                    timestamp = time.time() # Общий timestamp

                    if self.simulate:
                        # --- Логика симуляции Inclinometer ---
                        sim_time = timestamp - simulation_start_time
                        # Генерируем данные словарем
                        sim_data = {
                            'angle_roll': 10.0 * math.sin(2 * math.pi * sim_time / 30.0) + random.uniform(-0.1, 0.1),
                            'angle_pitch': 10.0 * math.cos(2 * math.pi * sim_time / 30.0) + random.uniform(-0.1, 0.1),
                            'elevation': 50.0 + sim_time * 0.1 + random.uniform(-0.5, 0.5),
                            'slope': 1.0 + sim_time * 0.01 + random.uniform(-0.05, 0.05),
                            'temp': 25.0 + math.sin(2 * math.pi * sim_time / 60.0)
                        }
                        self.inclinometer_data_queue.append((timestamp, sim_data))
                    elif self.inclinometer:
                         # --- Логика чтения реального Inclinometer ---
                        if not self.inclinometer.is_connected():
                            # Попытка переподключения
                            self.logger.warning(f"[{task_name}] Соединение потеряно. Попытка переподключения...")
                            try:
                                await self.inclinometer.connect()
                                if self.inclinometer.is_connected():
                                     self.logger.info(f"[{task_name}] Переподключение успешно.")
                                else:
                                     self.logger.error(f"[{task_name}] Не удалось переподключиться.")
                                     # Пауза перед следующей попыткой
                                     await asyncio.sleep(5.0) 
                                     continue
                            except Exception as conn_e:
                                 self.logger.error(f"[{task_name}] Ошибка при попытке переподключения: {conn_e}")
                                 await asyncio.sleep(5.0)
                                 continue
                             
                        self.logger.debug(f"[{task_name}] Calling inclinometer.read_data_async()...")
                        data = await self.inclinometer.read_data_async() 
                        self.logger.debug(f"[{task_name}] inclinometer.read_data_async() returned: {data is not None}")
                        if data:
                            self.inclinometer_data_queue.append((timestamp,data))
                            self.logger.debug(f"[{task_name}] Inclinometer read: {data}")

                except Exception as e:
                    log_func = self.logger.exception if not self.simulate  else self.logger.error
                    log_func(f"[{task_name}] Неизвестная ошибка в основной логике цикла {'чтения' if not self.simulate else 'симуляции'} Inclinometer: {e}")
                    # Пауза при неизвестной ошибке
                    await asyncio.sleep(5.0)
                finally:
                    # Расчет времени ожидания для поддержания частоты
                    inclinometer_interval_sec = 1.0 / self.inclinometer_freq_hz
                    await self._wait_for_next_cycle(start_time, inclinometer_interval_sec, task_name)
            
            except Exception as e:
                 # --- Внешняя обертка для ловли ВСЕХ исключений в цикле --- 
                self.logger.exception(f"[{task_name}] КРИТИЧЕСКАЯ НЕОБРАБОТАННАЯ ОШИБКА в главном цикле задачи! {e}")
                # Долгая пауза
                await asyncio.sleep(5.0)

        self.logger.info(f"[{task_name}] Задача завершена.")
        if not self.simulate and self.inclinometer:
            self.logger.info(f"[{task_name}] Закрываем соединение Inclinometer...")
            await self.inclinometer.close()
            self.logger.info(f"[{task_name}] Соединение Inclinometer закрыто.")

    async def _processing_task(self):
        """Асинхронная задача для обработки данных и отправки."""
        task_name = "ProcessingTask"
        self.logger.info(f"[{task_name}] Запуск задачи обработки данных...")
        if not self.zmq_handler:
            self.logger.error(f"[{task_name}] ZMQHandler не инициализирован, задача обработки не будет запущена.")
            return

        loop = asyncio.get_event_loop()

        while not self._shutdown_event.is_set():
            # Лог в начале цикла
            self.logger.debug(f"[{task_name}] Entering main loop iteration.")
            try:
                # --- Обертка для основного блока --- 
                start_time = loop.time()
                try:
                    current_time = time.time()
                    aggregation_start_time = current_time - self.aggregation_period_sec

                    adc_values_to_process = [val for ts, val in list(self.adc_data_queue) if ts >= aggregation_start_time]
                    inclinometer_dicts_to_process = [d for ts, d in list(self.inclinometer_data_queue) if ts >= aggregation_start_time]

                    avg_adc_raw = None
                    avg_inclinometer_data = None
                    message_to_send = None

                    # --- Усреднение ADC --- 
                    if adc_values_to_process:
                        try:
                            avg_adc_raw = statistics.mean(adc_values_to_process)
                            self.logger.debug(f"[{task_name}] ADC Avg Raw ({len(adc_values_to_process)} samples): {avg_adc_raw:.2f}")
                        except statistics.StatisticsError:
                            self.logger.warning(f"[{task_name}] Недостаточно данных ADC для усреднения ({len(adc_values_to_process)} < 1)")
                        except Exception as e:
                            self.logger.exception(f"[{task_name}] Ошибка усреднения ADC")

                    # --- Усреднение Inclinometer --- 
                    if inclinometer_dicts_to_process:
                        avg_inc_data_temp: Dict[str, float] = {}
                        # Определяем ключи для усреднения динамически
                        if inclinometer_dicts_to_process: 
                             keys_to_average = [k for k, v in inclinometer_dicts_to_process[0].items() if isinstance(v, (int, float))]
                        else:
                             keys_to_average = [] # если нет данных
                        
                        valid_samples_count: Dict[str, int] = {k: 0 for k in keys_to_average}

                        for data_dict in inclinometer_dicts_to_process:
                            for key in keys_to_average:
                                if key in data_dict and isinstance(data_dict[key], (int, float)):
                                    avg_inc_data_temp[key] = avg_inc_data_temp.get(key, 0) + data_dict[key]
                                    valid_samples_count[key] += 1

                        avg_inclinometer_data = {}
                        for key in keys_to_average:
                            if valid_samples_count[key] > 0:
                                avg_inclinometer_data[key] = avg_inc_data_temp[key] / valid_samples_count[key]
                            else:
                                avg_inclinometer_data[key] = None
                        
                        if avg_inclinometer_data:
                            self.logger.debug(f"[{task_name}] Inclinometer Avg ({len(inclinometer_dicts_to_process)} samples): {avg_inclinometer_data}")
                        elif inclinometer_dicts_to_process:
                            self.logger.warning(f"[{task_name}] Не найдено числовых ключей для усреднения в данных инклинометра.")

                    # --- Формирование и отправка сообщения --- 
                    if avg_adc_raw is not None or 
                                          avg_inclinometer_data is not None:
                        message_to_send = {
                            'timestamp': current_time,
                            'adc_avg_raw': avg_adc_raw, 
                            'inclinometer_avg': avg_inclinometer_data, 
                            'adc_samples': len(adc_values_to_process),
                            'inclinometer_samples': len(inclinometer_dicts_to_process)
                        }

                    if message_to_send:
                        try:
                            success = await self.zmq_handler.send_async(message_to_send)
                            if success:
                                self.logger.debug(f"[{task_name}] Sent: {message_to_send}")
                        except Exception as e:
                            self.logger.exception(f"[{task_name}] Неожиданная ошибка при вызове send_async: {e}")

                except Exception as e:
                    self.logger.exception(f"[{task_name}] Ошибка в основной логике цикла обработки данных: {e}")
                    await asyncio.sleep(1.0)
                finally:
                    await self._wait_for_next_cycle(start_time, self.processing_interval_sec, task_name)
            
            except Exception as e:
                self.logger.exception(f"[{task_name}] КРИТИЧЕСКАЯ НЕОБРАБОТАННАЯ ОШИБКА в главном цикле задачи! {e}")
                # Долгая пауза
                await asyncio.sleep(5.0)

        self.logger.info(f"[{task_name}] Задача обработки данных завершена.")
        # Закрываем ZMQ здесь
        if self.zmq_handler:
            try:
                if hasattr(self.zmq_handler, 'disconnect'):
                    await self.zmq_handler.disconnect()
                else:
                    self.zmq_handler.close()
            except Exception as e:
                self.logger.error(f"[{task_name}] Ошибка закрытия ZMQHandler: {e}")

    async def _wait_for_next_cycle(self, start_time: float, 
                                       interval_sec: float, task_name: str):
        """Рассчитывает и выполняет ожидание до следующего цикла задачи."""
        loop = asyncio.get_event_loop()
        elapsed_time = loop.time() - start_time
        wait_time = interval_sec - elapsed_time
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        # Логируем только если опоздание значительное (>10% интервала)
        # и интервал не слишком маленький, 
        # чтобы избежать спама при высокой частоте.
        elif interval_sec > 0.001 and wait_time < -interval_sec * 0.1:
             self.logger.warning(f"{task_name} task is lagging: elapsed {elapsed_time:.4f}s > interval {interval_sec:.4f}s")
        # Если wait_time <= 0, но опоздание небольшое, продолжаем без sleep

    async def async_init(self):
        """Асинхронная часть инициализации 
          (измерение частоты или установка по умолчанию)."""
        self.logger.info("Выполнение асинхронной инициализации...")

        if self.simulate:
            default_freq = 5.0
            inc_freq_str = str(self.config.get('sensors', {}) .get('inclinometer', {}) .get('frequency_hz', str(default_freq))).replace(',', '.')
            try:
                 self.inclinometer_freq_hz = float(inc_freq_str)
            except ValueError:
                 self.logger.warning(f"Не удалось преобразовать inclinometer frequency '{inc_freq_str}' в float для симуляции. Используется {default_freq}.")
                 self.inclinometer_freq_hz = default_freq
            if self.inclinometer_freq_hz <= 0: 
                self.inclinometer_freq_hz = default_freq
            self.logger.info(f"Режим симуляции: Частота инклинометра установлена в {self.inclinometer_freq_hz:.2f} Гц.")
        else:
            # В обычном режиме измеряем частоту
            measured_freq = await measure_inclinometer_frequency(self.config)
            if measured_freq is not None and measured_freq > 0:
                self.inclinometer_freq_hz = measured_freq
            else:
                # Используем значение по умолчанию или из конфига, 
                # если измерение не удалось
                default_freq = 5.0
                inc_freq_str = str(self.config.get('sensors', {}) .get('inclinometer', {}) .get('frequency_hz', str(default_freq))).replace(',', '.')
                try:
                    self.inclinometer_freq_hz = float(inc_freq_str)
                except ValueError:
                    self.logger.warning(f"Не удалось преобразовать inclinometer frequency '{inc_freq_str}' в float. Используется {default_freq}.")
                    self.inclinometer_freq_hz = default_freq
                if self.inclinometer_freq_hz <= 0: 
                    self.inclinometer_freq_hz = default_freq
                self.logger.warning(f"Не удалось измерить частоту инклинометра, используется значение {self.inclinometer_freq_hz:.2f} Гц.")

        # Пересчитываем размер очереди инклинометра и интервал
        self.inclinometer_queue_size = max(1, int(self.inclinometer_freq_hz * self.aggregation_period_sec))
        self.inclinometer_data_queue = deque(maxlen=self.inclinometer_queue_size)
        self.logger.info(f"Inclinometer: частота={self.inclinometer_freq_hz:.2f}Гц, очередь={self.inclinometer_queue_size} ({self.aggregation_period_sec}с)")

        self.logger.info(f"Processing: частота={self.processing_freq_hz}Гц, интервал={self.processing_interval_sec:.3f}с")
        self.logger.info("Асинхронная инициализация завершена.")

    async def run(self):
        """Запуск асинхронных задач системы."""
        self.logger.info("Запуск асинхронных задач...")
        self._shutdown_event.clear()
        self._tasks = []

        # --- Асинхронно подключаемся к ZMQ перед запуском задач --- 
        if self.zmq_handler:
             self.logger.debug("Run: Перед вызовом await self.zmq_handler.connect()")
             connect_success = await self.zmq_handler.connect()
             self.logger.debug(f"Run: После вызова await self.zmq_handler.connect(). Успех: {connect_success}")
             if not connect_success:
                  self.logger.error("Не удалось подключиться к ZMQ, но запуск продолжается (для отладки).")
        else:
             self.logger.warning("ZMQHandler не инициализирован.")

        # Запускаем задачи чтения и обработки
        self._tasks.append(asyncio.create_task(self._adc_reader_task(), name="ADCReader"))
        self._tasks.append(asyncio.create_task(self._inclinometer_reader_task(), name="InclinometerReader"))

        self._tasks.append(asyncio.create_task(self._processing_task(), name="ProcessingTask"))

        self.logger.info(f"Запущено {len(self._tasks)} задач. Система работает.")

        # Ожидаем завершения (например, по KeyboardInterrupt)
        await self._shutdown_event.wait()
        self.logger.info("Получено событие остановки. Завершение задач...")

        # Ожидаем завершения всех задач (с таймаутом)
        # Отмена не нужна, т.к. они должны завершиться по _shutdown_event
        done, pending = await asyncio.wait(self._tasks, timeout=5.0)

        if pending:
            self.logger.warning(f"Не все задачи завершились за 5 секунд: {pending}")
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    self.logger.info(f"Задача {task.get_name()} принудительно отменена.")
                except Exception as e:
                    self.logger.error(f"Ошибка при отмене задачи {task.get_name()}: {e}")

        self.logger.info("Все задачи завершены или отменены.")

    async def stop(self):
        """Инициирует остановку системы."""
        self.logger.info("Инициирована остановка системы...")
        self._shutdown_event.set()

def get_version_info() -> str:
    """Получает информацию о версии, комбинируя SemVer и данные о сборке.

    Использует данные из сгенерированного файла _build_info.py.
    В качестве запасного варианта читаем версию из метаданных пакета.
    """
    semver = _build_info.VERSION

    # Если _build_info не загрузился, пытаемся получить версию из метаданных
    if semver == "0.0.0-unknown-mock":
        try:
            semver = importlib.metadata.version('BBBsoft')
        except importlib.metadata.PackageNotFoundError:
            semver = "0.0.0-unknown"

    commit = _build_info.COMMIT_HASH
    date = _build_info.COMMIT_DATE

    # Формируем строку для вывода
    if commit != "unknown" and date != "unknown":
        # Убираем часовой пояс и 'T' из даты для краткости, если они есть
        date_short = date.split('+')[0].replace('T', ' ')
        # Убираем секунды для еще большей краткости
        try:
            date_parts = date_short.split(':')
            if len(date_parts) > 2:
                date_short = ':'.join(date_parts[:-1])
        except: pass # Игнорируем ошибки парсинга даты
        return f"{semver} (commit: {commit}, date: {date_short})"
    else:
        return semver # Возвращаем только SemVer, если данных о коммите нет

# --- Точка входа ---
async def main():
    system_version = get_version_info()
    parser = argparse.ArgumentParser(
        description="BBBsoft Measurement System",
        epilog=f"Версия программы: {system_version}"
    )
    parser.add_argument(
        '--simulate',
        action='store_true',
        help='Запуск симуляции без доступа к реальному оборудованию.'
    )
    parser.add_argument(
        '-V', '--version',
        action='version',
        version=f'%(prog)s {system_version}',
        help='Показать версию программы и выйти.'
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s:%(levelname)s - %(message)s')
    bootstrap_logger = logging.getLogger('BBBsoft_bootstrap')
    bootstrap_logger.info(f"Запуск BBBsoft asyncio... Версия: {system_version} (Режим симуляции: {'ВКЛ' if args.simulate else 'ВЫКЛ'})" )

    config = {}
    config_path = 'config/system_config.yaml'
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                loaded_config = yaml.safe_load(f)
                if loaded_config: config = loaded_config
        else:
            bootstrap_logger.warning(f"Файл конфигурации {config_path} не найден, используются внутренние значения по умолчанию MeasurementSystem.")
    except Exception as e:
        bootstrap_logger.exception(f"Ошибка загрузки конфигурации {config_path}: {e}. Используются внутренние значения по умолчанию.")

    system = None
    try:
        system = MeasurementSystem(config, simulate=args.simulate)
        await system.async_init()
        run_task = asyncio.create_task(system.run(), name="SystemRun")
        await asyncio.gather(run_task, return_exceptions=True)
    except KeyboardInterrupt:
        bootstrap_logger.info("Получен KeyboardInterrupt. Завершение...")
    except Exception as e:
        bootstrap_logger.exception("Критическая ошибка на верхнем уровне")
    finally:
        if system:
            bootstrap_logger.info("Отправка сигнала остановки системе...")
            await system.stop()

        if not args.simulate:
            try:
                import rcpy
                bootstrap_logger.info("Вызов rcpy.cleanup()...")
                rcpy.cleanup()
                bootstrap_logger.info("rcpy.cleanup() завершен.")
            except ImportError:
                bootstrap_logger.warning("rcpy не найден, пропуск cleanup.")
            except Exception as e:
                bootstrap_logger.error(f"Ошибка при вызове rcpy.cleanup(): {e}")

    bootstrap_logger.info(f"Приложение BBBsoft asyncio (Версия: {system_version}) завершило работу")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL ERROR during asyncio.run: {e}", file=sys.stderr) 