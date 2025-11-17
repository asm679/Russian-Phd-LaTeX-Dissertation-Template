import asyncio, zmq
import zmq.asyncio as zmq_async
import os, time, logging, json
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class ZMQHandler:
    """Асинхронный обработчик для отправки данных по ZeroMQ.

    Поддерживает обратную совместимость с конструктором вида ZMQHandler(config_dict).
    """
    def __init__(
        self,
        host: str = 'localhost',
        port: int = 5555,
        identity: str = "BBBsoft",
        socket_type: str = 'DEALER',
        *,
        save_local: bool = False,
        local_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        # Поддержка конструктора с config dict
        if isinstance(host, dict) and config is None:
            config = host  # type: ignore[assignment]
        if config is not None:
            self.protocol = config.get('protocol', 'tcp')
            self.host = config.get('host', 'localhost')
            self.port = int(config.get('port', 5555))
            self.identity = config.get('identity', identity)
            self.socket_type = config.get('socket_type', socket_type)
        else:
            self.protocol = 'tcp'
            self.host = host
            self.port = port
            self.identity = identity
            self.socket_type = socket_type

        # Путь и локальное сохранение
        self.save_local = save_local
        self.local_path = local_path or os.path.join(os.getcwd(), 'logs')

        # Тип сокета
        self.socket_type_enum = getattr(zmq, socket_type.upper(), zmq.DEALER)
        self.context: Optional[zmq_async.Context] = None
        self.socket: Optional[zmq_async.Socket] = None
        self._connection_lock = asyncio.Lock()
        self._is_connected = False

        logger.info(f"Async ZMQHandler инициализирован для {self.url} (Тип: {socket_type.upper()})")

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    async def connect(self) -> bool:
        """Устанавливает асинхронное соединение ZeroMQ."""
        async with self._connection_lock:
            if self._is_connected and self.socket and not self.socket.closed:
                logger.debug("ZMQ уже подключен.")
                return True

            try:
                # Закрываем старое соединение, если оно есть (синхронно)
                self.close()

                logger.info(f"ZMQ connect: Попытка подключения к {self.url}...")
                logger.debug(f"ZMQ connect: Перед созданием Context...")
                self.context = zmq_async.Context.instance()
                logger.debug(f"ZMQ connect: Context создан: {self.context}")
                
                logger.debug(f"ZMQ connect: Перед созданием Socket типа {self.socket_type_enum}...")
                self.socket = self.context.socket(self.socket_type_enum)
                logger.debug(f"ZMQ connect: Socket создан: {self.socket}")
                
                logger.debug(f"ZMQ connect: Перед установкой IDENTITY: {self.identity}")
                self.socket.setsockopt_string(zmq.IDENTITY, self.identity)
                
                logger.debug(f"ZMQ connect: Перед установкой LINGER: 0")
                self.socket.setsockopt(zmq.LINGER, 0)

                logger.debug(f"ZMQ connect: Перед вызовом self.socket.connect()...")
                self.socket.connect(self.url)
                logger.debug(f"ZMQ connect: Вызов self.socket.connect() завершен.")

                self._is_connected = True
                logger.info("ZMQ подключение успешно.")
                logger.debug(f"ZMQ connect: Установлен флаг _is_connected=True")
                return True
            except Exception as e:
                logger.exception(f"Ошибка подключения ZMQ: {e}")
                self.close()  # Попытка закрыть ресурсы при ошибке
                return False
                
    def is_connected(self) -> bool:
         """Проверяет статус соединения."""
         # Проверяем флаг и наличие сокета
         return self._is_connected and self.socket is not None and not self.socket.closed

    def send_data(self, sensor_type: str, data: Dict[str, Any]) -> bool:
        """
        Отправка данных через ZeroMQ 
        
        Args:
            sensor_type: Тип датчика (INC, Odometer, DSHK, GNSS, INS)
            data: Данные для отправки
            
        Returns:
            bool: Успешность отправки
        """
        try:
            # Добавляем метку времени
            data['time'] = time.time()
            
            # Формируем полный пакет
            message = {
                "identity": sensor_type,
                sensor_type: data
            }
            
            # Отправляем через ZMQ
            self.socket.send_json(message)
            return True
        except Exception as e:
            print(f"Ошибка отправки данных (send_data): {e}")
            return False

    async def send_async(self, message: Dict[str, Any]) -> bool:
        """Асинхронная отправка сообщения (словаря) через ZeroMQ."""
        if not self.is_connected():
            logger.warning("Попытка отправки ZMQ при отсутствии соединения. Попытка подключения...")
            if not await self.connect():
                logger.error("Не удалось подключиться для отправки ZMQ.")
                return False
                
        # Убедимся, что сокет существует после возможного реконнекта
        if not self.socket: return False 

        try:
            await self.socket.send_json(message)
            return True
        except zmq.ZMQError as e: # <-- Ловим базовый zmq.ZMQError
            logger.error(f"Ошибка ZMQ при отправке: {e} (Код: {e.errno})")
            if e.errno == zmq.EFSM:
                 logger.warning("Попытка переподключения ZMQ из-за ошибки состояния...")
                 self.close() # Закрываем перед реконнектом
            # Возвращаем False, чтобы вызывающий код знал об ошибке
            return False
        except Exception as e:
            logger.exception(f"Неожиданная ошибка при отправке ZMQ: {e}")
            self.close()
            return False

    async def disconnect(self):
        """Асинхронная обертка для закрытия."""
        self.close()

    def close(self):
        """Синхронное закрытие сокета и контекста ZeroMQ."""
        if self.socket and not self.socket.closed:
            logger.info("Закрытие ZMQ сокета...")
            try:
                self.socket.close()
            except Exception as e:
                logger.error(f"Ошибка при закрытии ZMQ сокета: {e}")
        self.socket = None
        self._is_connected = False
        self.context = None
        logger.debug("ZMQ ресурсы освобождены (сокет закрыт).")

    async def send_data(self, data: Dict[str, Any]) -> None:
    """Асинхронная отправка с формированием конверта.

    Под тесты ожидается структура {"timestamp": <float>, "data": <dict>}.
    Исключения не подавляются (пусть пробрасываются для тестов).
    """
        if not self.is_connected():
            await self.connect()
        if not self.socket:
            raise RuntimeError("ZMQ socket is not available")
        envelope = {"timestamp": time.time(), "data": data}
        await self.socket.send_json(envelope)

    # Вспомогательные методы для локального сохранения
    def _ensure_log_directory(self) -> None:
        if not os.path.exists(self.local_path):
            os.makedirs(self.local_path)

    def _save_data(self, data: Dict[str, Any]) -> None:
        if not self.save_local:
            return
        self._ensure_log_directory()
        file_path = os.path.join(self.local_path, "zmq_data.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Ошибка локального сохранения данных: {e}")