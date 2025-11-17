import numpy as np
from scipy.optimize import minimize_scalar

def clothoid_x(s: float, A: float) -> float:
    """Координата X клотоиды в локальной системе ( с точностью до 5- го порядка)."""
    return s - s**5 / (40 * A**4) + s**9 / (3456 * A**8)

def clothoid_y(s: float, A: float) -> float:
    """Координата Y клотоиды в локальной системе ( с точностью до 5- го порядка)."""
    return s**3 / (6 * A**2) - s**7 / (336 * A**6) + s**11 / (42240 * A**10)

def compute_rotation_angle(start: np.ndarray, end: np.ndarray, A: float, length: float) -> float:
    """Вычисляет угол поворота для совмещения локальной и глобальной систем координат."""
    # Конечная точка в локальной системе
    end_local = np.array([clothoid_x(length, A), clothoid_y(length, A)])
    # Угол между локальным и глобальным направлением
    global_vector = end - start
    local_vector = end_local - np.array([clothoid_x(0, A), clothoid_y(0, A)])
    return np.arctan2(global_vector[1], global_vector[0]) - np.arctan2(local_vector[1], local_vector[0])

def distance_to_clothoid(
    point: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    radius_start: float,
    radius_end: float,
    length: float
) -> float:
    # Вычисляем параметр A (для случая radiusStart = INF)
    A = np.sqrt(radius_end * length) if np.isinf(radius_start) else np.sqrt(radius_start * length)
    
    # Вычисляем угол поворота
    theta = compute_rotation_angle(start, end, A, length)
    rotation_matrix = np.array([
        [np.cos(theta), -np.sin(theta)],
        [np.sin(theta), np.cos(theta)]
    ])
    
    # Функция преобразования локальных координат в глобальные
    def local_to_global(s):
        x_local = clothoid_x(s, A)
        y_local = clothoid_y(s, A)
        return start + rotation_matrix @ np.array([x_local, y_local])
    
    # Целевая функция для минимизации
    def objective(s):
        clothoid_point = local_to_global(s)
        return np.linalg.norm(point - clothoid_point)
    
    # Поиск минимума на отрезке [0, length]
    result = minimize_scalar(objective, bounds=(0, length), method='bounded')
    s_opt = result.x
    closest_on_curve = local_to_global(s_opt)
    
    # Проверка граничных точек
    dist_start = np.linalg.norm(point - start)
    dist_end = np.linalg.norm(point - end)
    dist_curve = np.linalg.norm(point - closest_on_curve)
    
  # Определяем ближайшую точку из трех
    if dist_curve <= dist_start and dist_curve <= dist_end:
        return dist_curve, closest_on_curve
    elif dist_start <= dist_end:
        return dist_start, start
    else:
        return dist_end, end
