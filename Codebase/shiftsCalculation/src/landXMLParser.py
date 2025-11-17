import xml.etree.ElementTree as ET
import numpy as np
from tqdm import tqdm
from calculateSpiral import distance_to_clothoid
import traceback


class LandXMLParser:
    def __init__(self, filename):
        self.filename = filename
        self.track_segments = []
        self.profile_segments = []
        self.ns = {"lx": "http://www.landxml.org/schema/LandXML-1.1"}
        self.parse_landxml()

    def parse_landxml(self):
        print("Start parsing LandXML file...")
        tree = ET.parse(self.filename)
        root = tree.getroot()

        alignments = root.find("lx:Alignments", self.ns)
        if alignments is None:
            print("No <Alignments> found in the XML.")
            return

        for alignment in alignments.findall("lx:Alignment", self.ns):
            coord_geom = alignment.find("lx:CoordGeom", self.ns)
            if coord_geom is not None:
                for element in tqdm(coord_geom, desc="Парсинг", unit="сегменты"):
                    tag = element.tag.split("}")[-1]
                    sta_start = float(element.get("staStart", "0.0"))
                    
                    if tag == "Line":
                        start = element.find("lx:Start", self.ns)
                        end = element.find("lx:End", self.ns)
                        if start is not None and end is not None:
                            self.track_segments.append({
                                "type": "line",
                                "start": tuple(map(float, start.text.split())),
                                "end": tuple(map(float, end.text.split())),
                                "staStart": sta_start
                            })

                    elif tag == "Curve":
                        start = element.find("lx:Start", self.ns)
                        end = element.find("lx:End", self.ns)
                        center = element.find("lx:Center", self.ns)
                        radius = element.get("radius")
                        if start is not None and end is not None and center is not None and radius:
                            self.track_segments.append({
                                "type": "curve",
                                "start": tuple(map(float, start.text.split())),
                                "end": tuple(map(float, end.text.split())),
                                "center": tuple(map(float, center.text.split())),
                                "radius": float(radius),
                                "staStart": sta_start
                            })

                    elif tag == "Spiral":
                        start = element.find("lx:Start", self.ns)
                        end = element.find("lx:End", self.ns)
                        length = float(element.get("length", "0.0"))
                        radius_start = element.get("radiusStart")
                        radius_end = element.get("radiusEnd")
                        segment_data = {
                            "type": "spiral",
                            "start": tuple(map(float, start.text.split())),
                            "end": tuple(map(float, end.text.split())),
                            "length": length,
                            "staStart": sta_start
                        }
                        if radius_start: segment_data["radiusStart"] = float(radius_start)
                        if radius_end: segment_data["radiusEnd"] = float(radius_end)
                        self.track_segments.append(segment_data)

            profile = alignment.find("lx:Profile", self.ns)
            if profile is not None:
                for prof_align in tqdm(profile.findall("lx:ProfAlign", self.ns), desc="Парсинг профиля", unit="сегмент"):
                    name = prof_align.get("name")
                    if name == "Survey":
                        pvi_list = prof_align.findall("lx:PVI", self.ns)
                         for pvi in pvi_list:
                            try:
                                station, elevation = map(float, pvi.text.split())
                                self.profile_segments.append((station, elevation))
                            except Exception as e:
                                print(f"Ошибка в PVI: {pvi.text}")
                                traceback.print_exc()

        print(f"Parsed {len(self.track_segments)} track segments.")
        print(f"Parsed {len(self.profile_segments)} track profile segments.")

    def find_position(self, coords, H=0, delta_H=0, isInit=False, tolerance=10.0):
        """Ищет ближайшее положение на трассе с заданным допуском."""
        min_dist = float("inf")
        closest_point = None
        closest_segment = None
        delta_height = None
        point = np.array(coords)
        current_index = 0
        current_segment = None
        for index, segment in enumerate(self.track_segments):
            dist = float("inf")
            interpolated = None

            if segment["type"] == "line":
                start = np.array(segment["start"])
                end = np.array(segment["end"])
                vec = end - start
                vec_norm_sq = np.dot(vec, vec) + 1e-12
                t = np.clip(np.dot(point - start, vec) / vec_norm_sq, 0.0, 1.0)
                interpolated = start * (1 - t) + end * t
                station = self.get_station(segment, interpolated)
                height = self.get_elevation(station)
                dist = np.hypot(*(point - interpolated))
                real_delta_h = H - height

            elif segment["type"] == "curve":
                center = np.array(segment["center"])
                radius = segment["radius"]
                direction = (point - center) / (np.linalg.norm(point - center) + 1e-12)
                interpolated = center + direction * radius
                station = self.get_station(segment, interpolated)
                height = self.get_elevation(station)
                dist = np.hypot(*(point - interpolated))
                real_delta_h = H - height - delta_H

            elif segment["type"] == "spiral":
                start = np.array(segment["start"])
                end = np.array(segment["end"])
                radius_start = segment.get("radiusStart", 0.0)
                radius_end = segment.get("radiusEnd", 0.0)
                length = segment["length"]
                dist, interpolated = distance_to_clothoid(point, start, end, radius_start, radius_end, length)
                station = self.get_station(segment, interpolated)
                height = self.get_elevation(station)
                real_delta_h = H - height - delta_H

            else:
                continue

            if dist < min_dist:
                min_dist = dist
                closest_point = interpolated
                closest_segment = segment
                delta_height = real_delta_h
                current_segment = segment
                current_index = index

        if closest_point is not None:
            if not isInit:
                direction_sign = -1 if (point[0] - closest_point[0]) < 0 else 1
                station = self.get_station(closest_segment, closest_point)
                next_loc = self.track_segments[current_index + 1] if current_index + 1 < len(self.track_segments) else None
                return {
                    "dist": direction_sign * min_dist,
                    "delta_height": delta_height,
                    "height": self.get_elevation(station),
                    "current_loc": current_segment, 
                    "next_loc": next_loc,
                }
            return closest_point if min_dist <= tolerance else None

    def get_station(self, segment, interpolated):
        if segment["type"] == "line":
            start = np.array(segment["start"])
            end = np.array(segment["end"])
            vec = end - start
            seg_len = np.linalg.norm(vec)
            local_dist = np.dot(interpolated - start, vec) / (seg_len + 1e-12)
            return segment.get("staStart", 0.0) + local_dist

        elif segment["type"] == "curve":
            center = np.array(segment["center"])
            radius = segment["radius"]
            start = np.array(segment["start"])

            vec_start = start - center
            vec_point = interpolated - center

            angle_start = np.arctan2(vec_start[1], vec_start[0])
            angle_point = np.arctan2(vec_point[1], vec_point[0])

            delta_angle = angle_point - angle_start
            # Предположим, что направление обхода кривой - против часовой стрелки (CCW)
            if delta_angle < 0:
                delta_angle += 2 * np.pi

            arc_length = radius * delta_angle
            return segment.get("staStart", 0.0) + arc_length

        elif segment["type"] == "spiral":
            start = np.array(segment["start"])
            end = np.array(segment["end"])
            radius_start = segment.get("radiusStart", float('inf'))
            radius_end = segment.get("radiusEnd", float('inf'))
            length = segment["length"]

            dist_along, _ = distance_to_clothoid(interpolated, start, end, radius_start, radius_end, length)
            return segment.get("staStart", 0.0) + dist_along

        else:
            return segment.get("staStart", 0.0)

    def get_elevation(self, station):
        for i in range(1, len(self.profile_segments)):
            prev_station, prev_elev = self.profile_segments[i-1]
            next_station, next_elev = self.profile_segments[i]
            if prev_station <= station <= next_station:
                ratio = (station - prev_station) / (next_station - prev_station) if next_station != prev_station else 0
                return prev_elev + ratio * (next_elev - prev_elev)
        # Если вне диапазона — вернуть крайнее значение
        if not self.profile_segments:
            return 0.0
        return (
            self.profile_segments[-1][1]
            if station > self.profile_segments[-1][0]
            else self.profile_segments[0][1]
        )
