from zmqProcessor import ZMQProcessor
from landXMLParser import LandXMLParser
import os


def test_find_position(coords):
    script_dir = os.path.dirname(__file__)
    relative_path = "test.xml"
    absolute_path = os.path.join(script_dir, relative_path)
    parser = LandXMLParser(absolute_path)
    for coord in coords:
        result = parser.find_position(coord[0], coord[1], abs(coord[2]), isInit=False)
        print(f"Coordinates: {coord}, сдвижка: {result["dist"]}, высота: {result["height"]}, подъемка: {result["delta_height"]}")

def main():
    script_dir = os.path.dirname(__file__)
    relative_path = "ось.xml"
    absolute_path = os.path.join(script_dir, relative_path)
    parser = LandXMLParser(absolute_path)

    print(parser)
    zmq_processor = ZMQProcessor(5555, parser)
    
    if zmq_processor.initialize_position():
        print('onTrack')
        zmq_processor.listen()

test_coords = [
    [[-27.09323713, 31.75021417], 4.0, 0],  
    # Станция: 0 м (первая точка линии)
    # Ожидаемая высота: 0.0
    # Подъемка: 4.0 - 0.0 = 4.0

    [[-25.07773713, 28.67021417], 1.08, 0],  
    # Примерная станция: 3.6 м
    # Ожидаемая высота: 3.6 * 0.0225359 ≈ 0.0811
    # Подъемка: 1.08 - 0.0811 ≈ 0.9989

    [[-16.203, 14.849], 0.16, 0.5, 0],  
    # Примерная станция: ~20 м
    # Ожидаемая высота: 0.4507
    # Подъемка: 0.16 - 0.4507 ≈ -0.2907

    [[-15.87023713, 14.49171417], 0.16, 0],  
    # Примерная станция: ~21 м
    # Ожидаемая высота: 0.4507
    # Подъемка: 0.16 - 0.4507 ≈ -0.313
]
test_coords_for_testxml = [
    [[10.0, 0.0], 1.0, 0],      # Внутри первой прямой (станция ~10 м), высота ≈ 0.5, подъемка = 0.5
    [[25.0, 0.25], 1.75, -1],   # Внутри спирали (станция ~25 м), высота ≈ 1.25, подъемка = 0.5
    [[40.0, 0.75], 2.0, 0.5],   # Внутри круговой кривой (станция ~40 м), высота ≈ 2.0, подъемка = 0.0
    [[55.0, 1.0], 2.8, -1],     # Внутри последней прямой (станция ~55 м), высота ≈ 2.75, подъемка ≈ 0.05
]


if __name__ == "__main__":
    main()
#    test_find_position(test_coords_for_testxml)