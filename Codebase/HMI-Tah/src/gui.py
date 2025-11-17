from pathlib import Path
import threading 
from tkinter import Tk, Canvas, Text, Button, PhotoImage, Toplevel, Label, END
import serial
from pygeocom import PyGeoCom, LockInStatus, MeasurementProgram, TMCInclinationMode
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Arc
import xml.etree.ElementTree as ET
import time
import zmq
import json
import csv
import numpy as np
import sys
from pyclothoids import SolveG2

flag_start_visual = 0
flag_TAH_connection = 0
flag_connect_ok = 0
flag_retry = 1
flag_prism_lock = 0
flag_stop = 0
flag_app_end = 0
flag_established_connection = 0
flag_connect_lost = 0

next_status = 'None'
way_status = 'None'
next_coor = (0, 0)
offset_prism = [0.866179, 0.039005, 0.627931]
try:
    config = open('./resources/config.txt','r')
    data = json.load(config)

    com_port = data["Bluetooth_COM"]
    landxml_name = data["LandXML_Name"]
    result_name = data["Calculation_Name"]
    tah_data = data["Raw_tacheometer_data_Name"]
    beagle_data = data["Raw_sensor_data_Name"]
    m_DSHK = data["DSHK_scale"]
    DSHK_y_offset = data["DSHK_offset"]
    INC_roll_offset = data["INC_roll_offset"]
    INC_pitch_offset = data["INC_pitch_offset"]

    config.close()

except:
    config = open('./resources/config.txt','w')

    data = {"Bluetooth_COM": "COM6", 
            "LandXML_Name": "1", 
            "Calculation_Name": "result",
            "Raw_tacheometer_data_Name": "raw_data",
            "Raw_sensor_data_Name": "raw_Beagle_data",
            "DSHK_scale": 0,
            "DSHK_offset": 0,
            "INC_roll_offset": 0,
            "INC_pitch_offset":0}
    json.dump(data, config)

    config.close()

    com_port = data["Bluetooth_COM"]
    landxml_name = data["LandXML_Name"]
    result_name = data["Calculation_Name"]
    tah_data = data["Raw_tacheometer_data_Name"]
    beagle_data = data["Raw_sensor_data_Name"]
    m_DSHK = data["DSHK_scale"]
    DSHK_y_offset = data["DSHK_offset"]
    INC_roll_offset = data["INC_roll_offset"]
    INC_pitch_offset = data["INC_pitch_offset"]


lineList = []
spiralList = []
curveList = []

window_connection = None
lock_status = None

# Счетчик измерений
counter = 0

# Данные от датчиков
TAH_measurements = [[0,0,0]]
INC_data = [0, 0]
DSHK_data = [0]
rail_height = 0

# Расчеты сравнения Landxml
landxml_status = "off_track"
center_shift = 0
height_shift = 0

# Результаты расчета
L_R = [0, 0, 0]
C_R = [0, 0, 0]
R_R = [0, 0, 0]
path_W = 0

# Инициализация первого измерения тахеометра
TAH_last_x = 0
TAH_last_y = 0

# Вывод консоли
class ConsoleRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        self.text_widget.insert(END, message)
        self.text_widget.see(END)

    def flush(self):
        pass

# Подключение к BeagleBoneBlue для получения данных от инклинометра и датчика ширины колеи
def Beagle_connection():
    global counter, INC_data, DSHK_data, landxml_status, center_shift, way_status, next_status, next_coor, height_shift
    raw_B = ["DHSK", "INC_roll", "INC_pitch"]
    while True:
        
        if flag_app_end == 1:
            break 


        i, d_message = server_socket.recv_multipart()
        message = json.loads(d_message.decode())

        try:

            if message["inclinometer_avg"] is not None:
                INC_data = [message["inclinometer_avg"]["angle_roll"], message["inclinometer_avg"]["angle_pitch"]]
            else:
                print("Отсутствуют данные от инклинометра")

            if message["adc_avg_raw"] is not None:
                DSHK_data = [message["adc_avg_raw"]]
            else:
                print("Отсутвуют данные от датчика ширины колеи")
                

            # Запись сырых данных
            if flag_start_visual == 1 and flag_connect_ok == 1:
                f_raw_B = open(f'{beagle_data}.csv','a')
                
                csvwriter_raw = csv.writer(f_raw_B)
                csvwriter_raw.writerow(raw_B)
                raw_B = [DSHK_data[0], INC_data[0], INC_data[1]]
                
        except:
            
            landxml_status = message["status"]
            center_shift = float(message["shifts"])
            height_shift = float(message["height_dif"])
            try:

                way_status = message["current_loc"]['type']
                next_status = message["next_loc"]['type'] 
    
                next_coor = message["next_loc"]["start"]
                
            except:
                pass
                
            message_out = {"C_R_x": C_R[0], "C_R_y": C_R[1], "C_R_z": C_R[2], "H": rail_height/2}
            server_socket.send_multipart([i, json.dumps(message_out).encode()])
        time.sleep(0.05)

            

# Расчет параметров рельса и их запись
def calcute_params():
    global counter, TAH_last_x, TAH_last_y, L_R, C_R, R_R, path_W, rail_height
    raw = ["Tah_x", "Tah_y", "Tah_z"]
    result = ["Number", "Left_rail_x", "Left_rail_y", "Left_rail_z", "Center_x", "Center_y", "Center_z", "Right_rail_x", "Right_rail_y", "Right_rail_z", "shift", "height" ]

    while True:     
        
        if flag_start_visual == 1 and flag_connect_ok == 1:
            
            f_result = open(f'{result_name}.csv','a')
            f_raw = open(f'{tah_data}.csv','a')

            if (TAH_measurements[0][0] - TAH_last_x !=0) and (TAH_measurements[0][0] != 0):
                counter += 1

                try:
                    TAH_delta_x = TAH_measurements[0][0] - TAH_last_x
                    TAH_delta_y = TAH_measurements[0][1] - TAH_last_y
                except:
                    TAH_delta_x = 0
                    TAH_delta_y = 0

                
                if abs(TAH_delta_x) > 0.05 and abs(TAH_last_y) > 0.05:
                    psi = np.arctan2(TAH_delta_y,TAH_delta_x) 
                
                # Расчет угла крена
                roll = - np.radians(INC_data[0]-float(INC_roll_offset))

                # Расчет ширины колеи
                path_W = (DSHK_y_offset + m_DSHK*DSHK_data[0])
                
                try:
                    delta_L_x = (offset_prism[0] * np.cos(roll) + offset_prism[2] * np.sin(roll)) * np.cos(np.pi/2 - psi) + offset_prism[1] * np.cos(psi)
                    delta_L_y = - offset_prism[0] * np.sin(np.pi/2 - psi) + offset_prism[1] * np.sin(psi) 
                    delta_L_z = - offset_prism[0] * np.sin(roll) + offset_prism[2] * np.cos(roll)

                    delta_L = np.array([delta_L_x, delta_L_y, delta_L_z])

                    L_R = np.array(TAH_measurements[0]) - delta_L
                    
                    
                    delta_R_x = (- (path_W - offset_prism[0])* np.cos(roll) + offset_prism[2] * np.sin(roll)) * np.cos(np.pi/2 - psi) + offset_prism[1] * np.cos(psi)
                    delta_R_y = (path_W - offset_prism[0]) * np.sin(np.pi/2 - psi) + offset_prism[1] * np.sin(psi)
                    delta_R_z = (path_W - offset_prism[0]) * np.sin(roll) + offset_prism[2] * np.cos(roll)

                    delta_R = np.array([delta_R_x, delta_R_y, delta_R_z])

                    R_R = np.array(TAH_measurements[0]) - delta_R

                    delta_C_x = (- (path_W/2 - offset_prism[0])* np.cos(roll) + offset_prism[2] * np.sin(roll)) * np.cos(np.pi/2 - psi) + offset_prism[1] * np.cos(psi)
                    delta_C_y = (path_W/2 - offset_prism[0]) * np.sin(np.pi/2 - psi) + offset_prism[1] * np.sin(psi)
                    delta_C_z = (path_W/2 - offset_prism[0]) * np.sin(roll) + offset_prism[2] * np.cos(roll)
                    delta_C = np.array([delta_C_x, delta_C_y, delta_C_z])

                    C_R = np.array(TAH_measurements[0]) - delta_C

                    rail_height = R_R[2] - L_R[2]

                    csvwriter_raw = csv.writer(f_raw)
                    csvwriter_raw.writerow(raw)
                    raw = [TAH_measurements[0][0], TAH_measurements[0][1], TAH_measurements[0][2]]

                    csvwriter = csv.writer(f_result)
                    csvwriter.writerow(result)
                    result = [counter, round(L_R[0],4), round(L_R[1],4), round(L_R[2],4), round(C_R[0],4), round(C_R[1],4), round(C_R[2],4), round(R_R[0],4), round(R_R[1],4), round(R_R[2],4), center_shift, height_shift]
                except:
                    print("Для начала измерений начните движение")

                TAH_last_x = TAH_measurements[0][0]
                TAH_last_y = TAH_measurements[0][1]

        if flag_app_end == 1:
            break  

        time.sleep(0.05)    

# Подключение к тахеометру
def TAH_connection():
    def serial_connection(port, baudrate, cooldown = 9):
        start_time = time.time()
        while True:
            try:
                ser = serial.Serial(port=port, baudrate=baudrate, timeout=10)               
                return ser, 1
            
            except:
                pass

            if time.time() - start_time > cooldown:
                print("Ошибка при подключении к Тахеометру")
                break
            time.sleep(1)
        return None, 0
     
    global flag_TAH_connection, flag_start_visual, flag_retry, flag_prism_lock, TAH_measurements, flag_connect_ok, flag_established_connection, TAH_measurements, flag_connect_lost
    while True:
        
        if flag_start_visual == 1 and flag_retry == 1:
            try:
                geo.check_power()
            except:
                tah_ser, flag_TAH_connection = serial_connection(f'{com_port}', 19200)
                
            flag_retry = 0

            if flag_TAH_connection == 1:
                print("Соединение с тахеометром - Установлено")
                
                geo = PyGeoCom(tah_ser, debug = False)
                try:
                    geo.search(0.1, 0.1)
                    geo.user_lock_state_on()
                    geo.lock_in()
                    geo.set_measurement_program(MeasurementProgram.CONT_REF_STANDARD)
                    if geo.get_motor_lock_status()==LockInStatus.LOCKED_OUT:
                        flag_prism_lock = 0
                    else:
                        flag_prism_lock = 1
                except:
                    print("Наведите тахеометр на Призму")
                    
                
        if flag_connect_ok == 1:
            try:
                if geo.get_motor_lock_status()==LockInStatus.LOCKED_IN:

                    flag_prism_lock = 1
                    TAH_measurements = geo.get_coordinate(TMCInclinationMode.AUTOMATIC)
                    
                    time.sleep(1)
                elif geo.get_motor_lock_status()==LockInStatus.LOCKED_OUT:
                    flag_prism_lock = 0
                    print("Призма потеряна")
                    time.sleep(1)
                    try:
                        geo.search(0.1, 0.1)
                        geo.lock_in()
                    except:
                        print("Ошибка поиска призмы")
            except:
                print("Потеряно соединение с тахеометром")
                tah_ser.close()
                flag_TAH_connection = 0
                flag_connect_ok = 0
                flag_prism_lock = 0
                flag_established_connection = 0
                flag_connect_lost = 1
                time.sleep(1)

        if flag_app_end == 1:
            try:
                tah_ser.close()
            finally:
                break
        
        if flag_stop == 1:
            try:
                if flag_prism_lock == 1:
                    geo.user_lock_state_off()
            finally:
                flag_connect_ok = 0
                
                time.sleep(1)
        
        time.sleep(0.05)

# Открытие сокета сервера          
context = zmq.Context()
server_socket = context.socket(zmq.ROUTER)
server_socket.bind("tcp://*:5555")

# Поток подключения к тахеометру
thread1 = threading.Thread(target=TAH_connection)
thread1.start()

# Поток подключения к BeagleBone
thread2 = threading.Thread(target=Beagle_connection)
thread2.start()

# Поток расчета
thread3 = threading.Thread(target=calcute_params)
thread3.start()


OUTPUT_PATH = Path(__file__).parent
ASSETS_PATH = OUTPUT_PATH / Path(r"../resources/assets/frame0")

def relative_to_assets(path: str) -> Path:
    return ASSETS_PATH / Path(path)


#------------Ввод значений---------------------
def open_popup():
    top = Toplevel(window)
    top.configure(bg = "#141831")
    top.geometry("450x250")
    top.title("Ввод параметров")
    top.grab_set() 

    canvas_popup = Canvas(top, bg = "#151831", height = 250, width = 450, bd = 0, highlightthickness = 0, relief = "ridge")

    canvas_popup.place(x = 0, y = 0)
    input_image_1 = PhotoImage(file=relative_to_assets("input_image_1.png"))
    input_image1 = canvas_popup.create_image(150.0, 16.0, image=input_image_1)

    input_image_2 = PhotoImage(file=relative_to_assets("input_image_2.png"))
    input_image2 = canvas_popup.create_image(150.0, 46.0, image=input_image_2)

    input_image_3 = PhotoImage(file=relative_to_assets("input_image_3.png"))
    input_image3 = canvas_popup.create_image(150.0, 76.0, image=input_image_3)

    input_image_4 = PhotoImage(file=relative_to_assets("input_image_4.png"))
    input_image4 = canvas_popup.create_image(150.0, 106.0, image=input_image_4)

    input_image_5 = PhotoImage(file=relative_to_assets("input_image_5.png"))
    input_image5 = canvas_popup.create_image(150.0, 136.0, image=input_image_5)

    input_image_6 = PhotoImage(file=relative_to_assets("input_image_6.png"))
    input_image6 = canvas_popup.create_image(150.0, 166.0, image=input_image_6)



def change_start():
    
    def countdown(name, n):
        global flag_established_connection, flag_connect_lost, lock_status
        
        def connect_ok():
            global flag_connect_ok
            flag_connect_ok = 1
            button_start.config(state='disabled')
            window_connection.destroy()

        def retry(name):
            global flag_retry, lock_status
            flag_retry = 1
            countdown(name, 10)
            retry_button.destroy()
            close_button.destroy()
            try:
                lock_status.destroy()
            except:
                pass

        
        if n > 0:
            
            if flag_TAH_connection == 0:
                name.config(text=f"{n} сек...")
                window_connection.after(1000, countdown, name, n-1)
            elif flag_TAH_connection == 1 and flag_established_connection == 0: 
                name.config(text="Подключение установлено")
                flag_connect_lost = 0
                flag_established_connection = 1
                window_connection.after(1000, countdown, name, n-1)
            elif flag_TAH_connection == 1 and flag_established_connection == 1:
                start_time = time.time()
                while True:
                    if flag_prism_lock == 1:
                    
                        lock_status =  Label(window_connection, anchor="center", text="Цель захвачена", font=("Almarai Bold", 16 * -1))
                        lock_status.pack()
                        connect_ok_button = Button(window_connection, text="Начать измерение", anchor="center", font=("Almarai Bold", 16 * -1), command = lambda:connect_ok())
                        connect_ok_button.pack()
                        break
                    if time.time() - start_time > 5:
                        lock_status =  Label(window_connection, anchor="center", text="Цель не найдена", font=("Almarai Bold", 16 * -1))
                        lock_status.pack()
                        retry_button = Button(window_connection, text="Повтор", anchor="center", font=("Almarai Bold", 16 * -1), command = lambda:retry(name))
                        retry_button.pack()
                        close_button = Button(window_connection, text="Отмена", anchor="center", font=("Almarai Bold", 16 * -1), command=window_connection.destroy)
                        close_button.pack()
                        break
        else:
            name.config(text="Ошибка при подключении")
           
            close_button = Button(window_connection, text="Ок", anchor="center", font=("Almarai Bold", 16 * -1), command=window_connection.destroy)
            close_button.pack()

            retry_button = Button(window_connection, text="Переподключение", anchor="center", font=("Almarai Bold", 16 * -1), command = lambda:retry(name))
            retry_button.pack()
            
    global flag_start_visual, flag_stop, flag_retry, flag_connect_lost, window_connection
    
    if window_connection is not None and window_connection.winfo_exists():
        return
 
    flag_start_visual = 1
    flag_stop = 0
    flag_retry = 1

    popup_width = 250
    popup_height = 200
    screen_width = 1280
    screen_height = 720
    x = (screen_width // 2) - (popup_width // 2)
    y = (screen_height // 2) - (popup_height // 2)

    window_connection = Toplevel(window)
    window_connection.geometry(f"{popup_width}x{popup_height}+{x}+{y}")
    if flag_connect_lost == 0:
        window_connection.title("Инициализация...")
    elif flag_connect_lost == 1:
        window_connection.title("Переподключение...")
    window_connection.grab_set() 

    init_status = Label(window_connection, anchor="center", text="Подключение к тахеометру", font=("Almarai Bold", 16 * -1))
    init_status.pack()
    if flag_connect_lost == 1:
        init_status.config(text="Переподключение к тахеометру")
        button_start.config(state='normal')
    init_time =  Label(window_connection, anchor="center", text="Подключение установлено", font=("Almarai Bold", 16 * -1))
    init_time.pack()

    countdown(init_time, 10)
    
    window_connection.resizable(False, False)


def change_stop():
    global flag_start_visual, flag_start_write, flag_stop, flag_app_end
    button_start.config(state='active')
    flag_start_visual = 0
    flag_start_write = 0
    if flag_stop == 1:
        flag_app_end = 1
        window.destroy()
    flag_stop = 1

# Основное окно
    
window = Tk()
window.title("Параметры измерительного комплекса")
window.geometry("1280x720")
window.configure(bg = "#004689")

canvas = Canvas(window, bg = "#004689", height = 720, width = 1280, bd = 0, highlightthickness = 0, relief = "ridge")

canvas.place(x = 0, y = 0)

# Чтение LANDXML
try:
    track_segments = []
    tree = ET.parse(f'./resources/{landxml_name}.xml')
    root = tree.getroot()

    namespace_uri = root.tag[1:].split("}")[0]
    if namespace_uri == 'http://www.landxml.org/schema/LandXML-1.2':
        ns = '{http://www.landxml.org/schema/LandXML-1.2}'
        version = "1.2"
        print(version)
    elif namespace_uri == 'http://www.landxml.org/schema/LandXML-1.1':
        ns = '{http://www.landxml.org/schema/LandXML-1.1}'
        version = "1.1"
        print(version)
    else:
        print("Неподдерживающийся тип LandXML:", namespace_uri)

    for alignment in root.findall(ns + 'Alignments/' + ns + 'Alignment'):

        coordGeom = alignment.find(ns + 'CoordGeom')
        
        if coordGeom is not None:
        
            for obj in coordGeom:
                if obj.tag == ns + 'Line':
                    line_data = [
                        [*map(float, obj.find(ns + 'Start').text.split())],
                        [*map(float, obj.find(ns + 'End').text.split())]
                    ]
                    lineList.append(line_data)
                    
                elif obj.tag == ns + 'Spiral':
                    spiral_data = [
                        [*map(float, obj.find(ns + 'Start').text.split())],
                        [*map(float, obj.find(ns + 'PI').text.split())],
                        [*map(float, obj.find(ns + 'End').text.split())],
                        (obj.attrib['rot']),
                        (obj.attrib['radiusStart']),
                        (obj.attrib['radiusEnd'])
                    ]
                    spiralList.append(spiral_data)
                    
                elif obj.tag == ns + 'Curve':
                    curve_data = [
                        [*map(float, obj.find(ns + 'Start').text.split())],
                        [*map(float, obj.find(ns + 'End').text.split())],
                        [*map(float, obj.find(ns + 'Center').text.split())],
                        [(obj.attrib['rot'])]
                    ]
                    curveList.append(curve_data)

    # Построение плана на GUI 
    fig = Figure(figsize=(3,4), dpi = 120)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax = fig.add_subplot(111)

    ax.set_xlim((lineList[-1][0][0] + lineList[0][0][0])/2 - (3*(lineList[-1][0][1] - lineList[0][0][1] + (lineList[-1][0][1] - lineList[0][0][1])/10))/8, (lineList[-1][0][0] + lineList[0][0][0])/2 + (3*(lineList[-1][0][1] - lineList[0][0][1] + (lineList[-1][0][1] - lineList[0][0][1])/10))/8)
    ax.set_ylim(lineList[0][0][1] - (lineList[-1][0][1] - lineList[0][0][1])/20, lineList[-1][0][1] + (lineList[-1][0][1] - lineList[0][0][1])/20)


    for line in lineList:
        ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], 'b-')
        


    for spiral in spiralList:
        start = np.array([spiral[0][0], spiral[0][1]])
        end = np.array([spiral[2][0], spiral[2][1]])
        PI = np.array([spiral[1][0], spiral[1][1]])

        def angle_between_points(p1, p2):
            return np.arctan2(p2[1] - p1[1], p2[0] - p1[0])
        theta0 = angle_between_points(start, PI)
        theta1 = angle_between_points(PI, end)

        if spiral[5] == "INF":
            kappa0 = 0
        else:
            kappa0 = 1.0 / float(spiral[5])

        if spiral[4] == "INF":
            kappa1 = 0
        else:
            kappa1 = 1.0 / float(spiral[4])

        if spiral[3] == "cw":
            kappa1 = -abs(kappa1)

        clothoids = SolveG2(
            start[0],
            start[1],
            theta0,
            kappa0,
            end[0],
            end[1],
            theta1,
            kappa1
        )

        xs, ys = [], []
        for c in clothoids:
            x_sample, y_sample = c.SampleXY(200)
            xs.extend(x_sample)
            ys.extend(y_sample)

        ax.plot(xs, ys, 'r-')


    for curve in curveList:
        radius = (((curve[2][0]) - (curve[0][0]))**2 + ((curve[2][1]) - (curve[0][1]))**2)**0.5
        start = np.array([curve[0][0], curve[0][1]])
        end = np.array([curve[1][0], curve[1][1]])
        vec = end - start
        chord_length = np.linalg.norm(vec)

        midpoint = (start + end) / 2
        perp_vec = np.array([-vec[1], vec[0]])
        perp_vec = perp_vec / np.linalg.norm(perp_vec)

        h = np.sqrt(radius**2 - (chord_length / 2)**2)

        if curve[3][0] == 'ccw':
            center = midpoint - h * perp_vec
        else:
            center = midpoint + h * perp_vec

        def angle(point):
            return np.degrees(np.arctan2(point[1] - center[1], point[0] - center[0]))

        theta1 = angle(start)
        theta2 = angle(end)
        ax.set_aspect('equal')
        if curve[3][0] == 'ccw':
            arc = Arc(center, 2*radius, 2*radius, angle=0, theta1=theta2, theta2=theta1, color='green', lw=2)
        else:
            arc = Arc(center, 2*radius, 2*radius, angle=0, theta1=theta1, theta2=theta2, color='green', lw=2)
        ax.add_patch(arc)

    scatter_pos = ax.scatter(lineList[0][0][0], lineList[0][0][1], c='green')

    fig.gca().set_facecolor('black')
    ax.spines['top'].set_visible(False)
    ax.spines['bottom'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.tick_params(axis='both', which='both', labelbottom=False, labelleft=False, bottom=False, left=False)
    ax.grid(True)

    plan_plt = FigureCanvasTkAgg(fig, master = window)
    plan_plt.draw()
    plan_plt.get_tk_widget().place(x=49.0, y=168.0)
except:
    print("No LANDXML-file")


    # Текущая позиция 0

# Возвышение рельса
image_image_100 = PhotoImage(file=relative_to_assets("blind_big.png"))
# image_110
image_100 = canvas.create_image(1009, 330.0, image=image_image_100)

up_right = canvas.create_text((989+1064)/2, (294+324)/2, anchor="center", text="", fill="#000000", font=("Almarai Bold", 15 * -1))

image_image_110 = PhotoImage(file=relative_to_assets("blind_big.png")) 
# image_111
image_110 = canvas.create_image(615.5, 330.0, image=image_image_110)

up_left = canvas.create_text((589+665)/2, (294+324)/2, anchor="center", text="", fill="#000000", font=("Almarai Bold", 16 * -1))

# Изображения для GUI
image_image_1 = PhotoImage(file=relative_to_assets("image_1.png"))
image_1 = canvas.create_image(812.0, 395.0, image=image_image_1)

image_image_2 = PhotoImage(file=relative_to_assets("image_2.png"))
image_2 = canvas.create_image(813.0, 502.0, image=image_image_2)

image_image_3 = PhotoImage(file=relative_to_assets("image_3.png"))
image_3 = canvas.create_image(808.0, 569.0, image=image_image_3)

image_image_4 = PhotoImage(file=relative_to_assets("image_4.png"))
image_4 = canvas.create_image(773.0, 322.0, image=image_image_4)

# Координаты оси пути
canvas.create_rectangle(583.0, 584.0, 733.0, 614.0, fill="#D9D9D9", outline="black", width=2)
text_x_center = canvas.create_text((583+733)/2, (584+614)/2 ,anchor="center",text="0",fill="#000000",font=("Almarai Bold", 18 * -1))

canvas.create_rectangle(733.0, 584.0, 883.0, 614.0,fill="#D9D9D9", outline="black", width=2)
text_y_center = canvas.create_text((733+883)/2, (584+614)/2, anchor="center", text="0", fill="#000000", font=("Almarai Bold", 18 * -1))

canvas.create_rectangle(883.0, 584.0, 1033.0, 614.0, fill="#D9D9D9", outline="black", width=2)
text_z_center = canvas.create_text((883+1033)/2, (584+614)/2, anchor="center", text="0", fill="#000000", font=("Almarai Bold", 18 * -1))

# Ширина колеи
canvas.create_rectangle(848.0, 307.0, 923.0, 337.0, fill="#D9D9D9", outline="black", width=2)
text_rail_width = canvas.create_text((848+923)/2, (307+337)/2, anchor="center", text="0", fill="#000000", font=("Almarai Bold", 15 * -1))

# Кнопка "Старт"
button_image_start = PhotoImage(file=relative_to_assets("button_1.png"))
button_start = Button(image=button_image_start, borderwidth=0, activebackground="#004689",  highlightthickness=0, command=change_start, relief="flat")
button_start.place(x=1120.0, y=668.0, width=125.0, height=35.0)

# Кнопка "Стоп"
button_image_stop = PhotoImage(file=relative_to_assets("button_3.png"))
button_stop = Button(image=button_image_stop, borderwidth=0, activebackground="#004689", highlightthickness=0, command=change_stop, relief="flat")
button_stop.place(x=47.0, y=669.0, width=125.0, height=35.0)

image_image_5 = PhotoImage(file=relative_to_assets("image_5.png"))
image_5 = canvas.create_image(229.0, 408.0, image=image_image_5)

image_image_6 = PhotoImage(file=relative_to_assets("image_6.png"))
image_6 = canvas.create_image(812.0, 352.0, image=image_image_6)

# Подъемка оси пути
image_image_7 = PhotoImage(file=relative_to_assets("image_7.png"))
image_7 = canvas.create_image(633, 517, image=image_image_7)

# Сдвижка оси пути
image_image_8 = PhotoImage(file=relative_to_assets("image_8.png"))
image_image_87 = PhotoImage(file=relative_to_assets("image_87.png")) # Влево
image_image_88 = PhotoImage(file=relative_to_assets("image_88.png")) # Вправо
image_8 = canvas.create_image(995.0, 517.0, image=image_image_8)

# Сдвижка 
image_image_9 = PhotoImage(file=relative_to_assets("image_9.png"))
image_9 = canvas.create_image(748.0, 484.0, image=image_image_9)

canvas.create_rectangle(647, 502, 722, 532, fill="#D9D9D9", outline="black", width=2)
text_right_rail_up = canvas.create_text((647+722)/2, (502+532)/2, anchor="center", text="0", fill="#000000", font=("Almarai Bold", 15 * -1))

# Подъемка
image_image_10 = PhotoImage(file=relative_to_assets("image_10.png"))
image_10 = canvas.create_image(878.0, 484.0, image=image_image_10)

canvas.create_rectangle(906, 502, 981, 532, fill="#D9D9D9", outline="black", width=2)
text_right_rail_side = canvas.create_text((906+981)/2, (502+532)/2, anchor="center", text="0", fill="#000000", font=("Almarai Bold", 15 * -1))

image_image_11 = PhotoImage(file=relative_to_assets("image_11.png"))
image_11 = canvas.create_image(640.0, 68.0, image=image_image_11)

image_image_12 = PhotoImage(file=relative_to_assets("image_12.png"))
image_12 = canvas.create_image(662.0, 216.0, image=image_image_12)

image_image_13 = PhotoImage(file=relative_to_assets("image_13.png"))
image_13 = canvas.create_image(587.0, 246.0, image=image_image_13)

# Тип следующего участка пути
image_image_14 = PhotoImage(file=relative_to_assets("image_14.png"))
image_14 = canvas.create_image(737.0, 246.0, image=image_image_14)

text_next_status = canvas.create_text((662+812)/2, (231+261)/2, anchor="center", text=" ", fill="#000000", font=("Almarai Bold", 22 * -1))

# Расстояние до следующего участка пути
image_image_15 = PhotoImage(file=relative_to_assets("image_15.png"))
image_15 = canvas.create_image(962.0, 246.0, image=image_image_15)

text_next_way = canvas.create_text((812+1112)/2, (231+261)/2, anchor="center", text="метров", fill="#000000", font=("Almarai Bold", 22 * -1))

# Текущий участок пути
image_image_16 = PhotoImage(file=relative_to_assets("image_16.png"))
image_16 = canvas.create_image(962.0, 216.0, image=image_image_16)

text_way_status = canvas.create_text((812+1112)/2, (201+231)/2, anchor="center", text=" ", fill="#000000", font=("Almarai Bold", 22 * -1))

# Точка положения на плане
plan_pos = canvas.create_oval(222.0, 401.0, 232.0, 411.0, fill="lightcoral", outline="red")

# Текущее время
image_image_24 = PhotoImage(file=relative_to_assets("image_24.png"))
image_24 = canvas.create_image(1031.0, 156.0, image=image_image_24)

canvas.create_rectangle(1105.0, 141.0, 1255.0, 171.0, fill="#D9D9D9", outline="")
text_time = canvas.create_text((1105+1255)/2, (141+171)/2, anchor="center", text="00:00:00", fill="#000000", font=("Almarai Bold", 22 * -1))

# Консоль
consol_box = Text(window, wrap='word', height=50, width=430, bg='black', fg='white', font=('Consolas', 10))
consol_box.place(x=(733+883)/2, y=(584+614)/2 + 50, anchor="center", width=450.0, height= 60.0)

sys.stdout = ConsoleRedirector(consol_box)
sys.stderr = ConsoleRedirector(consol_box)

image_bind_110 = PhotoImage(file=relative_to_assets("image_111.png"))
image_bind_100 = PhotoImage(file=relative_to_assets("image_110.png"))
image_blind = PhotoImage(file=relative_to_assets("blind_big.png"))

#------------------обновление значений окон---------------------------------
def update_pos():
    #Time
    current_time = time.strftime("%H:%M:%S")
    canvas.itemconfig(text_time, text=f"{current_time}")

    global counter, flag_connect_lost, plan_plt, ax, scatter_pos

    if flag_start_visual == 1 and flag_connect_lost == 1 and flag_retry == 0:
        change_start()
        
    if flag_start_visual == 1 and flag_connect_ok == 1:

        x_center = round(C_R[0], 4)
        y_center = round(C_R[1], 4)
        z_center = round(C_R[2], 4)

        right_rail_side = round(center_shift,1)

        right_rail_up = round(height_shift,1)

        rail_width = round((path_W*10**(3)),1)

        # Смена возвышения
        try:
            if rail_height < 0:
                canvas.itemconfig(up_left , text=f"{round(-rail_height*1000,2)} мм")
                canvas.itemconfig(up_right , text=f"")
                canvas.itemconfig(image_110, image=image_bind_110)
                canvas.itemconfig(image_100, image=image_blind)
            else:
                canvas.itemconfig(up_left , text=f"")
                canvas.itemconfig(up_right , text=f"{round(rail_height*1000,2)} мм")
                canvas.itemconfig(image_110, image=image_blind)
                canvas.itemconfig(image_100, image=image_bind_100)
        except:
            print("Ошибка при смене возвышения")

        if landxml_status == "on_track":

            next_way = round(((next_coor[0] - C_R[0])**2 + (next_coor[1] - C_R[1])**2)**0.5, 3)

            if next_status == 'line':
                next_t_status = "прямой"
            elif next_status == 'curve':
                next_t_status = "кривой"
            else:
                next_t_status = "ПК"

            if way_status == 'line':
                cur_w = "прямая"
            elif way_status == 'curve':
                cur_w = "кривая"
            else:
                cur_w = "переходная кривая"
        else:
            next_way = "No LANDXML"
            next_t_status = "No LANDXML"
            cur_w = "No LANDXML"

        # Сдвижка и подъемка
        try:
            if center_shift < 0:
                canvas.itemconfig(image_8, image=image_image_87)
                canvas.itemconfig(text_right_rail_side, text=f"{-right_rail_side} м")
            else:
                canvas.itemconfig(image_8, image=image_image_88)
                canvas.itemconfig(text_right_rail_side, text=f"{right_rail_side} м")
        except:
            print("Ошибка при смене сдвижки")
        
        
        canvas.itemconfig(text_right_rail_up, text=f"{right_rail_up} м")

        # Ширина колеи
        canvas.itemconfig(text_rail_width, text=f"{rail_width} мм")

        # Тип следующего участок пути
        canvas.itemconfig(text_next_status, text=f"{next_t_status}")

        # Расстояние до следующего участка пути
        canvas.itemconfig(text_next_way, text=f"{next_way} метров")

        # Текущий тип участка пути
        canvas.itemconfig(text_way_status, text=f"{cur_w}")

        # Координаты оси пути
        canvas.itemconfig(text_x_center, text=f"{x_center} м")
        canvas.itemconfig(text_y_center, text=f"{y_center} м")
        canvas.itemconfig(text_z_center, text=f"{z_center} м")

        # Обновление плана
        try:
            try:
                scatter_pos.remove() 
            finally:
                ax.set_xlim(C_R[0] - 0.75*25, C_R[0] + 0.75*25)
                ax.set_ylim(C_R[1] - 25, C_R[1] + 25)
                scatter_pos = ax.scatter(C_R[0], C_R[1], c='green')
                plan_plt.draw()
        except:
            pass

        if flag_prism_lock == 0:
            canvas.itemconfig(text_x_center, text=f"")
            canvas.itemconfig(text_y_center, text=f"Цель потеряна")
            canvas.itemconfig(text_z_center, text=f"")

    window.after(100, update_pos)

def app_end():
    global flag_app_end
    flag_app_end = 1
    server_socket.close() 
    thread1.join()   
    thread2.join()
    thread3.join()
    window.destroy()

update_pos()
window.resizable(False, False)
window.protocol("WM_DELETE_WINDOW", app_end)
window.mainloop()

if flag_app_end == 1:
    app_end()
    
