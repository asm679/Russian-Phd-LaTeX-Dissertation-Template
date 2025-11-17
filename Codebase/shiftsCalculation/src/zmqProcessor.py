import zmq, time, json
import numpy as np

class ZMQProcessor:
    def __init__(self, port, parser):
        self.port = port
        self.parser = parser
        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.DEALER)
        self.socket.connect("tcp://192.168.8.133:5555")  
        self.socket.setsockopt_string(zmq.IDENTITY, "Tacheometer") 
        self.current_segment = None

    def initialize_position(self):
        """Принимает первую координату и определяет, находится ли объект на трассе."""
        self.socket.send(json.dumps({"status": "out_of_track", "shifts": '0', "height_dif": "0", 
                    "current_loc": "soon?", 
                    "next_loc": "soon?"}).encode())
        message = self.socket.recv()
        # Декодируем байтовую строку в обычную строку
        message_str = message.decode('utf-8')

        # Парсим JSON строку в словарь Python
        message_dict = json.loads(message_str)
        coords = [ message_dict["C_R_x"],message_dict["C_R_y"]]
        if (coords[0] == 0 and coords[1] == 0):
            time.sleep(1)
            self.initialize_position()
            return
        print(coords)
        position = self.parser.find_position(coords, 0, 0, isInit=True)
        
        if position is None:
            self.socket.send(json.dumps({"status": "out_track", "shifts": '0', "height_dif": "0", 
                    "current_loc": "soon?", 
                    "next_loc": "soon?"}).encode())
            print('out')
            return True
        
        else:
            self.current_segment = position
            self.socket.send(json.dumps({"status": "on_track", "shifts": '0', "height_dif": "0", 
                    "current_loc": "soon?", 
                    "next_loc": "soon?"}).encode())
            print('on')
            return True

    def listen(self):
        """Слушает входящие координаты после инициализации."""
        while True:
            message = self.socket.recv()
            # Декодируем байтовую строку в обычную строку
            message_str = message.decode('utf-8')
            # Парсим JSON строку в словарь Python
            message_dict = json.loads(message_str)
            coords = [ message_dict["C_R_x"], message_dict["C_R_y"]]
            H = message_dict["C_R_z"]
            delta_h = message_dict["H"]
            print(message)
            calc_data = self.parser.find_position(coords, H, abs(delta_h), isInit=False)
            dist = calc_data['dist']
            height_dif = calc_data['delta_height']
            current_loc = calc_data['current_loc']
            next_loc = calc_data['next_loc']
            time.sleep(1)
            if dist:
                self.socket.send(json.dumps({
                    "status": "on_track", 
                    "shifts": dist, 
                    "height_dif": height_dif, 
                    "current_loc": current_loc, 
                    "next_loc": next_loc})
                    .encode())
                time.sleep(1)
