#!/usr/bin/env python3
"""
하이브리드 Zone-based Multimodal RAPTOR
- 30x30 zone 그리드 기반
- 대중교통 RAPTOR + 모빌리티 동적 연결
- Lazy evaluation으로 메모리 효율화
- 사용자 설정 가능한 거리별 전략
"""

import pickle
import json
import numpy as np
import networkx as nx
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque
from enum import Enum
import logging
import time
import math
from pathlib import Path

# 기존 모듈 import
try:
    from PART1_2 import Stop, Route, Trip
    from PART2_NEW import TransportMode, JourneyType, RoutePreference, TimeExpandedMultimodalRAPTOR
except ImportError as e:
    print(f"Import error: {e}")
    print("PART1_2.py와 PART2_NEW.py가 필요합니다.")
    exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@dataclass
class Zone:
    """30x30 그리드의 각 구역"""
    id: str  # "Z_15_20" 형식
    row: int
    col: int
    bounds: Dict[str, float]  # north, south, east, west
    transit_stops: List[str] = field(default_factory=list)
    mobility_density: float = 0.5
    zone_type: str = "mixed"  # residential, commercial, mixed

@dataclass
class RoutingStrategy:
    """거리별 라우팅 전략"""
    zone_distance: int
    strategy_name: str
    mobility_weight: float
    transit_weight: float
    allow_direct_mobility: bool = True
    check_direct_transit: bool = True
    max_transfers: int = 2

@dataclass
class ZoneConfig:
    """사용자 설정 가능한 구역 설정"""
    grid_size: Tuple[int, int] = (30, 30)
    
    # 거리별 전략 (사용자가 수정 가능)
    distance_strategies: Dict[int, Tuple[str, float, float]] = field(default_factory=lambda: {
        0: ("mobility_only", 1.0, 0.0),      # 같은 구역
        1: ("mobility_first", 0.8, 0.2),     # 인접 구역
        2: ("mobility_preferred", 0.7, 0.3), # 2구역 차이
        3: ("balanced", 0.5, 0.5),           # 3구역 차이
        4: ("transit_preferred", 0.3, 0.7),  # 4구역 차이
        5: ("transit_first", 0.2, 0.8),      # 5구역 차이
        "default": ("transit_only", 0.1, 0.9) # 6구역 이상
    })
    
    # 간편 설정
    mobility_only_threshold: int = 2      # 이 거리 이하는 모빌리티만
    mobility_preferred_threshold: int = 4  # 이 거리 이하는 모빌리티 우선
    
    # 시간대별 조정
    rush_hour_penalty: float = 0.7        # 러시아워 모빌리티 페널티
    late_night_bonus: float = 1.3         # 심야 모빌리티 보너스

class HybridZoneRAPTOR:
    """하이브리드 Zone 기반 멀티모달 RAPTOR"""
    
    def __init__(self, 
                 data_dir: str = 'gangnam_raptor_data',
                 virtual_stations_dir: str = 'grid_virtual_stations',
                 config: ZoneConfig = None):
        """초기화"""
        self.data_dir = Path(data_dir)
        self.virtual_stations_dir = Path(virtual_stations_dir)
        self.config = config or ZoneConfig()
        
        # Zone 그리드
        self.zones: Dict[str, Zone] = {}
        self.zone_grid: np.ndarray = None
        
        # 대중교통 RAPTOR (PART2_NEW 활용)
        self.transit_raptor: TimeExpandedMultimodalRAPTOR = None
        self.transit_data = None
        
        # OSM 도로 네트워크
        self.road_network = None
        
        # 모빌리티 데이터 (Lazy loading)
        self.kickboard_zones: List[Dict] = []
        self.bike_stations: List[Dict] = []
        self.zone_mobility_cache: Dict[str, Dict] = {}  # 캐시
        
        # Zone 연결 캐시
        self.zone_connections_cache: Dict[Tuple[str, str], List] = {}
        self.road_distance_cache: Dict[Tuple[Tuple[float, float], Tuple[float, float]], float] = {}
        
        # 초기화
        self._initialize()
    
    def _initialize(self):
        """시스템 초기화"""
        logger.info("하이브리드 Zone RAPTOR 초기화 시작...")
        
        # 1. 대중교통 데이터 로드
        self._load_transit_data()
        
        # 2. OSM 도로 네트워크 로드
        self._load_road_network()
        
        # 3. Zone 그리드 생성
        self._create_zone_grid()
        
        # 4. 모빌리티 데이터 로드
        self._load_mobility_data()
        
        # 5. Zone별 대중교통 정류장 매핑
        self._map_transit_to_zones()
        
        logger.info(f"초기화 완료: {len(self.zones)} zones, "
                   f"{len(self.transit_data['stops'])} 대중교통 정류장")
    
    def _load_transit_data(self):
        """대중교통 RAPTOR 데이터 로드"""
        raptor_file = self.data_dir / 'raptor_data.pkl'
        if not raptor_file.exists():
            raise FileNotFoundError("RAPTOR 데이터 없음. PART1_2.py 먼저 실행하세요.")
        
        with open(raptor_file, 'rb') as f:
            self.transit_data = pickle.load(f)
        
        # PART2_NEW의 TimeExpandedMultimodalRAPTOR 초기화 (로그 억제)
        import logging as temp_logging
        root_logger = temp_logging.getLogger()
        original_level = root_logger.level
        root_logger.setLevel(temp_logging.WARNING)
        
        try:
            self.transit_raptor = TimeExpandedMultimodalRAPTOR(data_path=str(self.data_dir))
        finally:
            root_logger.setLevel(original_level)
        
        logger.info(f"대중교통 데이터 로드: {len(self.transit_data['stops'])} 정류장")
    
    def _load_road_network(self):
        """OSM 도로 네트워크 로드"""
        try:
            # pkl 파일 우선 시도
            if Path("gangnam_road_network.pkl").exists():
                with open("gangnam_road_network.pkl", 'rb') as f:
                    self.road_network = pickle.load(f)
                logger.info(f"OSM 도로 네트워크 로드 완료 (pkl) - {len(self.road_network.nodes)} 노드, {len(self.road_network.edges)} 엣지")
            elif Path("gangnam_road_network.graphml").exists():
                self.road_network = nx.read_graphml("gangnam_road_network.graphml")
                logger.info("OSM 도로 네트워크 로드 완료 (graphml)")
            else:
                logger.warning("OSM 도로 네트워크 파일이 없습니다. 직선거리를 사용합니다.")
        except Exception as e:
            logger.warning(f"OSM 도로 네트워크 로드 실패: {e}")
            self.road_network = None
    
    def _create_zone_grid(self):
        """30x30 Zone 그리드 생성"""
        # 강남 경계 (실제 데이터 기반)
        bounds = {
            'north': 37.5500,
            'south': 37.4600,
            'west': 127.0000,
            'east': 127.1400
        }
        
        rows, cols = self.config.grid_size
        lat_step = (bounds['north'] - bounds['south']) / rows
        lon_step = (bounds['east'] - bounds['west']) / cols
        
        self.zone_grid = np.zeros((rows, cols), dtype=object)
        
        for row in range(rows):
            for col in range(cols):
                zone_id = f"Z_{row:02d}_{col:02d}"
                
                zone = Zone(
                    id=zone_id,
                    row=row,
                    col=col,
                    bounds={
                        'north': bounds['north'] - row * lat_step,
                        'south': bounds['north'] - (row + 1) * lat_step,
                        'east': bounds['west'] + (col + 1) * lon_step,
                        'west': bounds['west'] + col * lon_step
                    }
                )
                
                self.zones[zone_id] = zone
                self.zone_grid[row, col] = zone
        
        logger.info(f"Zone 그리드 생성: {rows}x{cols} = {len(self.zones)} zones")
    
    def _load_mobility_data(self):
        """모빌리티 데이터 로드 (경량화)"""
        import pandas as pd
        
        # 가상 정거장 데이터 (스윙 주행 데이터 기반으로 생성된)
        # 500개 또는 300개 버전 중 사용 (500개가 기본)
        virtual_stations_file = self.virtual_stations_dir / 'virtual_stations_500.csv'
        if not virtual_stations_file.exists():
            virtual_stations_file = self.virtual_stations_dir / 'virtual_stations_300.csv'
        
        if virtual_stations_file.exists():
            stations_df = pd.read_csv(virtual_stations_file)
            for _, row in stations_df.iterrows():
                self.kickboard_zones.append({
                    'id': row['station_id'],
                    'lat': row['center_lat'],
                    'lon': row['center_lon'],
                    'name': row['station_name'],
                    'demand': row.get('demand', 0),
                    'n_kickboards': row.get('n_kickboards', 0)
                })
            logger.info(f"가상 정거장 파일 로드: {virtual_stations_file.name}")
        
        # 따릉이 데이터 - transit_raptor의 bike_stations에서 가져오기
        if hasattr(self.transit_raptor, 'bike_stations') and self.transit_raptor.bike_stations:
            # transit_raptor의 bike_stations는 딕셔너리 형태
            for station_id, station_info in self.transit_raptor.bike_stations.items():
                if isinstance(station_info, dict):
                    # coords가 있는 경우
                    if 'coords' in station_info:
                        self.bike_stations.append({
                            'id': station_id,
                            'lat': station_info['coords'][0],
                            'lon': station_info['coords'][1],
                            'name': station_info.get('name', station_id)
                        })
                    # lat, lon이 직접 있는 경우
                    elif 'lat' in station_info and 'lon' in station_info:
                        self.bike_stations.append({
                            'id': station_id,
                            'lat': station_info['lat'],
                            'lon': station_info['lon'],
                            'name': station_info.get('name', station_id)
                        })
        
        # 따릉이가 없으면 transit_data에서 찾기 (stop_type=2)
        if not self.bike_stations:
            for stop_id, stop in self.transit_data['stops'].items():
                if hasattr(stop, 'stop_type') and stop.stop_type == 2:  # 따릉이
                    self.bike_stations.append({
                        'id': stop_id,
                        'lat': stop.stop_lat,
                        'lon': stop.stop_lon,
                        'name': stop.stop_name
                    })
        
        logger.info(f"모빌리티 데이터 로드: {len(self.kickboard_zones)} PM 가상 정거장, "
                   f"{len(self.bike_stations)} 따릉이 역")
    
    def _map_transit_to_zones(self):
        """대중교통 정류장을 Zone에 매핑"""
        for stop_id, stop in self.transit_data['stops'].items():
            zone = self._get_zone_for_location(stop.stop_lat, stop.stop_lon)
            if zone:
                zone.transit_stops.append(stop_id)
        
        # Zone별 밀도 계산
        for zone in self.zones.values():
            if len(zone.transit_stops) > 10:
                zone.zone_type = "commercial"
                zone.mobility_density = 0.8
            elif len(zone.transit_stops) > 5:
                zone.zone_type = "mixed"
                zone.mobility_density = 0.5
            else:
                zone.zone_type = "residential"
                zone.mobility_density = 0.3
    
    def _get_zone_for_location(self, lat: float, lon: float) -> Optional[Zone]:
        """위도/경도에 해당하는 Zone 찾기"""
        for zone in self.zones.values():
            if (zone.bounds['south'] <= lat <= zone.bounds['north'] and
                zone.bounds['west'] <= lon <= zone.bounds['east']):
                return zone
        return None
    
    def _calculate_zone_distance(self, zone1: Zone, zone2: Zone) -> int:
        """두 Zone 간 거리 계산 (체스판 거리)"""
        return max(abs(zone1.row - zone2.row), abs(zone1.col - zone2.col))
    
    def _get_routing_strategy(self, zone_distance: int) -> RoutingStrategy:
        """거리에 따른 라우팅 전략 결정"""
        strategies = self.config.distance_strategies
        
        if zone_distance in strategies:
            name, mobility_w, transit_w = strategies[zone_distance]
        else:
            name, mobility_w, transit_w = strategies["default"]
        
        # 시간대별 조정
        current_hour = datetime.now().hour
        if 7 <= current_hour <= 9 or 18 <= current_hour <= 20:  # 러시아워
            mobility_w *= self.config.rush_hour_penalty
            transit_w = 1 - mobility_w
        elif 22 <= current_hour or current_hour <= 5:  # 심야
            mobility_w *= self.config.late_night_bonus
            mobility_w = min(mobility_w, 1.0)
            transit_w = 1 - mobility_w
        
        return RoutingStrategy(
            zone_distance=zone_distance,
            strategy_name=name,
            mobility_weight=mobility_w,
            transit_weight=transit_w,
            allow_direct_mobility=(zone_distance <= self.config.mobility_only_threshold),
            check_direct_transit=(zone_distance >= 2),
            max_transfers=min(2, max(0, zone_distance - 2))
        )
    
    def find_routes(self, 
                   origin: Tuple[float, float],
                   destination: Tuple[float, float],
                   departure_time: str = "08:00",
                   preference: RoutePreference = None) -> List[Dict]:
        """하이브리드 경로 탐색"""
        start_time = time.time()
        
        if preference is None:
            preference = RoutePreference()
        
        # 1. Zone 확인
        origin_zone = self._get_zone_for_location(origin[0], origin[1])
        dest_zone = self._get_zone_for_location(destination[0], destination[1])
        
        if not origin_zone or not dest_zone:
            logger.warning("출발지/도착지가 강남 지역 밖입니다.")
            return []
        
        # 2. Zone 거리 계산 및 전략 결정
        zone_distance = self._calculate_zone_distance(origin_zone, dest_zone)
        strategy = self._get_routing_strategy(zone_distance)
        
        # 디버깅 정보 출력
        print(f"출발 Zone: {origin_zone.id} (row={origin_zone.row}, col={origin_zone.col})")
        print(f"도착 Zone: {dest_zone.id} (row={dest_zone.row}, col={dest_zone.col})")
        print(f"Zone 거리: {zone_distance}, 전략: {strategy.strategy_name}")
        print(f"모빌리티 가중치: {strategy.mobility_weight:.2f}, "
              f"대중교통 가중치: {strategy.transit_weight:.2f}")
        
        # 3. 전략에 따른 경로 탐색
        routes = []
        
        # 3-1. 같은 Zone이거나 매우 가까운 경우
        if strategy.allow_direct_mobility:
            mobility_routes = self._find_direct_mobility_routes(
                origin, destination, origin_zone, dest_zone, departure_time
            )
            routes.extend(mobility_routes)
        
        # 3-2. 대중교통 필요한 경우
        if strategy.transit_weight > 0:
            transit_routes = self._find_hybrid_routes(
                origin, destination, origin_zone, dest_zone, 
                departure_time, strategy, preference
            )
            routes.extend(transit_routes)
        
        # 4. 경로 점수 계산 및 정렬
        self._calculate_route_scores(routes, preference, strategy)
        routes.sort(key=lambda r: r['score'], reverse=True)
        
        elapsed = time.time() - start_time
        print(f"\n✅ 경로 탐색 완료: {len(routes)}개 경로, {elapsed:.2f}초")
        
        return routes[:5]  # 상위 5개 반환
    
    def _find_direct_mobility_routes(self, origin: Tuple, destination: Tuple,
                                    origin_zone: Zone, dest_zone: Zone,
                                    departure_time: str) -> List[Dict]:
        """모빌리티만 사용하는 직접 경로"""
        routes = []
        distance = self._haversine_distance(
            origin[0], origin[1], destination[0], destination[1]
        )
        
        # 킥보드 경로
        if distance <= 3000:  # 3km 이하
            # OSM 도로 거리 계산 (킥보드)
            road_distance = self._get_road_distance(
                origin[0], origin[1], 
                destination[0], destination[1],
                mode='kickboard'
            )
            routes.append({
                'type': 'mobility_only',
                'mode': 'kickboard',
                'segments': [{
                    'mode': 'kickboard',
                    'from': origin,
                    'to': destination,
                    'distance': road_distance,
                    'duration': road_distance / 333,  # 20km/h
                    'cost': 1000 + int(road_distance / 100) * 200
                }],
                'total_time': road_distance / 333,
                'total_cost': 1000 + int(road_distance / 100) * 200,
                'transfers': 0,
                'walk_distance': 0
            })
        
        # 따릉이 경로 (가까운 대여소 찾기)
        nearby_bikes = self._find_nearby_bike_stations(origin, 500)
        if nearby_bikes:
            bike_station = nearby_bikes[0]
            walk_dist = bike_station['distance']  # 이미 OSM 도로 거리
            # 따릉이 구간도 OSM 도로 거리 사용
            bike_dist = self._get_road_distance(
                bike_station['lat'], bike_station['lon'],
                destination[0], destination[1],
                mode='bike'
            )
            
            routes.append({
                'type': 'mobility_only',
                'mode': 'bike',
                'segments': [
                    {
                        'mode': 'walk',
                        'from': origin,
                        'to': (bike_station['lat'], bike_station['lon']),
                        'distance': walk_dist,
                        'duration': walk_dist / 80  # 80m/분
                    },
                    {
                        'mode': 'bike',
                        'from': (bike_station['lat'], bike_station['lon']),
                        'to': destination,
                        'distance': bike_dist,
                        'duration': bike_dist / 250,  # 15km/h
                        'cost': 1000  # 따릉이 요금
                    }
                ],
                'total_time': walk_dist / 80 + bike_dist / 250,
                'total_cost': 1000,
                'transfers': 0,
                'walk_distance': walk_dist
            })
        
        return routes
    
    def _find_hybrid_routes(self, origin: Tuple, destination: Tuple,
                           origin_zone: Zone, dest_zone: Zone,
                           departure_time: str, strategy: RoutingStrategy,
                           preference: RoutePreference) -> List[Dict]:
        """대중교통 + 모빌리티 하이브리드 경로"""
        routes = []
        
        # Zone 경계의 환승 포인트 찾기 (Lazy)
        access_points = self._get_zone_access_points(origin_zone, origin)
        egress_points = self._get_zone_access_points(dest_zone, destination)
        
        if not access_points or not egress_points:
            return routes
        
        # 대중교통 RAPTOR 실행
        dep_minutes = self._time_to_minutes(departure_time)
        
        for access in access_points[:1]:  # 상위 1개 접근점만
            for egress in egress_points[:1]:
                # 대중교통 경로 탐색 (PART2_NEW 방식)
                # 정류장 좌표 찾기
                access_stop = self.transit_data['stops'][access['stop_id']]
                egress_stop = self.transit_data['stops'][egress['stop_id']]
                
                # PART2_NEW의 find_routes 사용 (verbose 끄기)
                # 임시로 로거 레벨 변경
                import logging as temp_logging
                root_logger = temp_logging.getLogger()
                original_level = root_logger.level
                root_logger.setLevel(temp_logging.WARNING)
                
                try:
                    transit_journeys = self.transit_raptor.find_routes(
                        origin=(access_stop.stop_lat, access_stop.stop_lon),
                        destination=(egress_stop.stop_lat, egress_stop.stop_lon),
                        departure_time=departure_time,
                        journey_type=JourneyType.TRANSIT_ONLY,
                        preference=preference
                    )
                finally:
                    # 원래 레벨로 복원
                    root_logger.setLevel(original_level)
                
                # Journey를 route 형식으로 변환
                transit_routes = []
                for journey in transit_journeys[:2]:
                    # Journey의 legs에서 대중교통 구간 정보 추출
                    transit_legs = [leg for leg in journey.legs if leg.get('type') == 'transit']
                    transit_routes.append({
                        'duration': journey.total_time,
                        'transfers': journey.transfers,
                        'cost': journey.total_cost,
                        'transit_legs': transit_legs  # 대중교통 구간 상세 정보
                    })
                
                if not transit_routes:
                    continue
                
                # First/Last mile 추가
                for t_route in transit_routes[:2]:  # 각 경로 상위 2개
                    hybrid_route = self._build_hybrid_route(
                        origin, destination, 
                        access, egress, t_route,
                        strategy
                    )
                    routes.append(hybrid_route)
        
        return routes
    
    def _get_zone_access_points(self, zone: Zone, 
                               location: Tuple[float, float]) -> List[Dict]:
        """Zone의 접근점 찾기 (Lazy evaluation)"""
        cache_key = (zone.id, f"{location[0]:.4f},{location[1]:.4f}")
        
        if cache_key in self.zone_connections_cache:
            return self.zone_connections_cache[cache_key]
        
        access_points = []
        
        # 1. Zone 내 대중교통 정류장
        for stop_id in zone.transit_stops:
            stop = self.transit_data['stops'][stop_id]
            # OSM 도로 거리 사용
            distance = self._get_road_distance(
                location[0], location[1],
                stop.stop_lat, stop.stop_lon,
                mode='walk'
            )
            
            if distance <= 800:  # 800m 이내
                access_points.append({
                    'stop_id': stop_id,
                    'stop_name': stop.stop_name,
                    'distance': distance,
                    'time': distance / 80,  # 도보 시간 (분)
                    'mode': 'walk'
                })
        
        # 2. 가까운 모빌리티로 접근 가능한 정류장
        if zone.mobility_density > 0.5:
            nearby_stops = self._find_mobility_accessible_stops(
                location, zone, max_distance=1500
            )
            access_points.extend(nearby_stops)
        
        # 거리순 정렬
        access_points.sort(key=lambda x: x['time'])
        
        # 캐시 저장
        self.zone_connections_cache[cache_key] = access_points[:5]
        
        return access_points[:5]
    
    def _find_mobility_accessible_stops(self, location: Tuple,
                                       zone: Zone, max_distance: float) -> List[Dict]:
        """모빌리티로 접근 가능한 정류장 찾기"""
        accessible = []
        
        # 킥보드로 접근
        for stop_id in zone.transit_stops:
            stop = self.transit_data['stops'][stop_id]
            # 먼저 직선거리로 필터링
            straight_distance = self._haversine_distance(
                location[0], location[1],
                stop.stop_lat, stop.stop_lon
            )
            
            if 300 < straight_distance <= max_distance * 1.2:  # 300m 이하는 도보가 효율적, 킥보드는 300m 이상에서만 제공
                # OSM 도로 거리 계산
                distance = self._get_road_distance(
                    location[0], location[1],
                    stop.stop_lat, stop.stop_lon,
                    mode='kickboard'
                )
                
                if distance <= max_distance:
                    accessible.append({
                        'stop_id': stop_id,
                        'stop_name': stop.stop_name,
                        'distance': distance,
                        'time': distance / 333,  # 킥보드 시간
                        'mode': 'kickboard',
                        'cost': 1000 + int(distance / 100) * 200
                    })
        
        return accessible
    
    def _build_hybrid_route(self, origin: Tuple, destination: Tuple,
                           access: Dict, egress: Dict, 
                           transit_route: Dict,
                           strategy: RoutingStrategy) -> Dict:
        """하이브리드 경로 구성"""
        segments = []
        total_time = 0
        total_cost = 0
        walk_distance = 0
        
        # First mile
        if access['mode'] == 'walk':
            segments.append({
                'type': 'first_mile',
                'mode': 'walk',
                'from': origin,
                'to': access['stop_name'],
                'duration': access['time'],
                'distance': access['distance']
            })
            walk_distance += access['distance']
        else:
            segments.append({
                'type': 'first_mile',
                'mode': access['mode'],
                'from': origin,
                'to': access['stop_name'],
                'duration': access['time'],
                'distance': access['distance'],
                'cost': access.get('cost', 0)
            })
            total_cost += access.get('cost', 0)
        
        total_time += access['time']
        
        # Transit segments - 상세 정보 포함
        transit_legs = transit_route.get('transit_legs', [])
        if transit_legs:
            # 여러 대중교통 구간이 있을 경우 모두 추가
            for leg in transit_legs:
                segments.append({
                    'type': 'transit',
                    'mode': 'transit',
                    'route_name': leg.get('route_name', '대중교통'),
                    'from': leg.get('from', access['stop_name']),
                    'to': leg.get('to', egress['stop_name']),
                    'duration': leg.get('duration', 10),
                    'cost': leg.get('cost', 0) if segments[-1]['type'] != 'transit' else 0  # 첫 탑승만 요금
                })
                # 환승이 있으면 환승 구간 추가
                if leg != transit_legs[-1]:  # 마지막 구간이 아니면
                    segments.append({
                        'type': 'transfer',
                        'mode': 'walk',
                        'from': leg.get('to'),
                        'to': '환승',
                        'duration': 2,  # 환승 시간
                        'distance': 100  # 환승 거리
                    })
        else:
            # 상세 정보가 없으면 기존 방식
            segments.append({
                'type': 'transit',
                'mode': 'transit',
                'from': access['stop_name'],
                'to': egress['stop_name'],
                'duration': transit_route.get('duration', 30),
                'transfers': transit_route.get('transfers', 0),
                'cost': transit_route.get('cost', 1250)
            })
        total_time += transit_route.get('duration', 30)
        total_cost += transit_route.get('cost', 1250)
        
        # Last mile
        if egress['mode'] == 'walk':
            segments.append({
                'type': 'last_mile',
                'mode': 'walk',
                'from': egress['stop_name'],
                'to': destination,
                'duration': egress['time'],
                'distance': egress['distance']
            })
            walk_distance += egress['distance']
        else:
            segments.append({
                'type': 'last_mile', 
                'mode': egress['mode'],
                'from': egress['stop_name'],
                'to': destination,
                'duration': egress['time'],
                'distance': egress['distance'],
                'cost': egress.get('cost', 0)
            })
            total_cost += egress.get('cost', 0)
        
        total_time += egress['time']
        
        return {
            'type': 'hybrid',
            'strategy': strategy.strategy_name,
            'segments': segments,
            'total_time': total_time,
            'total_cost': total_cost,
            'transfers': transit_route.get('transfers', 0),
            'walk_distance': walk_distance
        }
    
    def _calculate_route_scores(self, routes: List[Dict], 
                               preference: RoutePreference,
                               strategy: RoutingStrategy):
        """경로 점수 계산"""
        if not routes:
            return
        
        # 정규화를 위한 최대/최소값
        times = [r['total_time'] for r in routes]
        costs = [r['total_cost'] for r in routes]
        transfers = [r['transfers'] for r in routes]
        walks = [r['walk_distance'] for r in routes]
        
        min_time, max_time = min(times), max(times) if max(times) > min(times) else min(times) + 1
        min_cost, max_cost = min(costs), max(costs) if max(costs) > min(costs) else min(costs) + 1
        min_transfers, max_transfers = min(transfers), max(transfers) if max(transfers) > min(transfers) else 1
        min_walk, max_walk = min(walks), max(walks) if max(walks) > min(walks) else 1
        
        for route in routes:
            # 각 요소 점수 (0-1, 높을수록 좋음)
            time_score = 1 - (route['total_time'] - min_time) / (max_time - min_time)
            cost_score = 1 - (route['total_cost'] - min_cost) / (max_cost - min_cost)
            transfer_score = 1 - (route['transfers'] - min_transfers) / (max_transfers - min_transfers)
            walk_score = 1 - (route['walk_distance'] - min_walk) / (max_walk - min_walk)
            
            # 전략에 따른 추가 점수
            strategy_bonus = 0
            if route['type'] == 'mobility_only' and strategy.mobility_weight > 0.7:
                strategy_bonus = 0.1
            elif route['type'] == 'hybrid' and 0.3 < strategy.mobility_weight < 0.7:
                strategy_bonus = 0.1
            
            # 모빌리티 선호도 반영
            mobility_bonus = 0
            if route['type'] == 'mobility_only':
                # 어떤 모빌리티를 사용했는지 확인
                for seg in route['segments']:
                    if seg['mode'] == 'bike':
                        mobility_bonus = 0.1 * preference.mobility_preference.get('bike', 0.8)
                    elif seg['mode'] == 'kickboard':
                        mobility_bonus = 0.1 * preference.mobility_preference.get('kickboard', 0.6)
            
            # 최종 점수
            route['score'] = (
                preference.time_weight * time_score +
                preference.cost_weight * cost_score +
                preference.transfer_weight * transfer_score +
                preference.walk_weight * walk_score +
                strategy_bonus +
                mobility_bonus
            )
    
    def _find_nearby_bike_stations(self, location: Tuple,
                                  max_distance: float) -> List[Dict]:
        """가까운 따릉이 역 찾기"""
        nearby = []
        
        for station in self.bike_stations:
            # OSM 도로 거리 사용 (도보)
            distance = self._get_road_distance(
                location[0], location[1],
                station['lat'], station['lon'],
                mode='walk'
            )
            
            if distance <= max_distance:
                nearby.append({
                    **station,
                    'distance': distance
                })
        
        nearby.sort(key=lambda x: x['distance'])
        return nearby
    
    def _haversine_distance(self, lat1: float, lon1: float,
                           lat2: float, lon2: float) -> float:
        """두 지점 간 거리 계산 (미터)"""
        R = 6371000  # 지구 반지름 (미터)
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _get_road_distance(self, lat1: float, lon1: float, 
                          lat2: float, lon2: float, mode: str = 'walk') -> float:
        """OSM 도로 네트워크를 이용한 실제 거리 계산"""
        # 캐시 확인
        cache_key = ((lat1, lon1), (lat2, lon2))
        if cache_key in self.road_distance_cache:
            return self.road_distance_cache[cache_key]
        
        straight_distance = self._haversine_distance(lat1, lon1, lat2, lon2)
        
        # OSM 네트워크가 없거나 거리가 너무 멀면 직선거리 * 1.3
        if not self.road_network or straight_distance > 2000:
            road_distance = straight_distance * 1.3
            self.road_distance_cache[cache_key] = road_distance
            return road_distance
        
        try:
            # 가장 가까운 노드 찾기
            min_dist1 = float('inf')
            min_dist2 = float('inf')
            nearest_node1 = None
            nearest_node2 = None
            
            for node, data in self.road_network.nodes(data=True):
                # node가 문자열인 경우가 있을 수 있음
                if 'y' in data and 'x' in data:
                    node_lat = data['y']
                    node_lon = data['x']
                    
                    dist1 = self._haversine_distance(lat1, lon1, node_lat, node_lon)
                    dist2 = self._haversine_distance(lat2, lon2, node_lat, node_lon)
                    
                    if dist1 < min_dist1:
                        min_dist1 = dist1
                        nearest_node1 = node
                    
                    if dist2 < min_dist2:
                        min_dist2 = dist2
                        nearest_node2 = node
            
            # 도로 네트워크 상 최단 경로 계산
            if nearest_node1 and nearest_node2 and nearest_node1 != nearest_node2:
                try:
                    path_length = nx.shortest_path_length(
                        self.road_network, 
                        nearest_node1, 
                        nearest_node2, 
                        weight='length'
                    )
                    # 시작/끝 지점까지의 거리 추가
                    road_distance = path_length + min_dist1 + min_dist2
                    
                    # 모드별 보정 (도보는 더 짧은 경로 가능)
                    if mode == 'walk' and straight_distance <= 300:
                        road_distance = min(road_distance, straight_distance * 1.2)
                    
                    self.road_distance_cache[cache_key] = road_distance
                    return road_distance
                except nx.NetworkXNoPath:
                    pass
        except Exception as e:
            logger.debug(f"도로 거리 계산 실패: {e}")
        
        # 실패시 직선거리 * 1.3
        road_distance = straight_distance * 1.3
        self.road_distance_cache[cache_key] = road_distance
        return road_distance
    
    def _time_to_minutes(self, time_str: str) -> int:
        """시간 문자열을 분으로 변환"""
        parts = time_str.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    
    def update_zone_config(self, new_config: Dict):
        """사용자가 Zone 설정 업데이트"""
        if 'distance_strategies' in new_config:
            self.config.distance_strategies.update(new_config['distance_strategies'])
        
        if 'mobility_only_threshold' in new_config:
            self.config.mobility_only_threshold = new_config['mobility_only_threshold']
        
        if 'mobility_preferred_threshold' in new_config:
            self.config.mobility_preferred_threshold = new_config['mobility_preferred_threshold']
        
        logger.info("Zone 설정이 업데이트되었습니다.")


def main():
    """테스트 실행"""
    # 모든 로거의 레벨을 WARNING으로 설정
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger('PART2_NEW').setLevel(logging.WARNING)
    
    print("하이브리드 Zone-based Multimodal RAPTOR")
    print("=" * 50)
    print("\n📋 현재 설정:")
    print("  - Zone 거리 전략: 2구역까지 모빌리티 우선")
    print("  - 모빌리티 선호도:")
    print("    • 따릉이: 90% (매우 선호)")
    print("    • 킥보드: 40% (덜 선호)")
    print("    • 전기자전거: 70% (보통)")
    
    # 사용자 설정 예시
    custom_config = ZoneConfig()
    # 2구역까지는 모빌리티만, 4구역까지는 모빌리티 우선
    custom_config.mobility_only_threshold = 2
    custom_config.mobility_preferred_threshold = 4
    
    # 초기화
    hybrid_raptor = HybridZoneRAPTOR(config=custom_config)
    
    # 테스트 경로들 (올바른 좌표)
    test_routes = [
        {
            'name': '신사역 → 압구정역 (3호선)',
            'origin': (37.5164, 127.0201),  # 신사역 (3호선)
            'dest': (37.5270, 127.0286),   # 압구정역 (3호선)
            'expected': 'balanced'
        }
    ]
    
    # 사용자 선호도
    preference = RoutePreference(
        time_weight=0.4,
        cost_weight=0.1,
        transfer_weight=0.3,
        walk_weight=0.2,
        max_walk_distance=800,
        mobility_preference={
            'bike': 0.9,      # 따릉이 매우 선호 (0.8 → 0.9)
            'kickboard': 0.4,  # 킥보드 덜 선호 (0.6 → 0.4)
            'ebike': 0.7      # 전기자전거 보통 선호
        }
    )
    
    # 첫 번째 테스트만 실행
    test = test_routes[0]
    print(f"\n🚀 {test['name']}")
    print("-" * 50)
    
    routes = hybrid_raptor.find_routes(
        test['origin'],
        test['dest'],
        departure_time="08:30",
        preference=preference
    )
    
    if routes:
        print(f"\n📍 찾은 경로: {len(routes)}개\n")
        
        for i, route in enumerate(routes[:3]):
            strategy_name = route.get('strategy', '직접 이동' if route['type'] == 'mobility_only' else 'N/A')
            print(f"경로 {i+1} ({route['type']}, 전략: {strategy_name})")
            print(f"  총 시간: {route['total_time']:.1f}분")
            print(f"  총 비용: {route['total_cost']:,}원")
            print(f"  환승: {route['transfers']}회")
            print(f"  도보: {route['walk_distance']:.0f}m")
            print(f"  점수: {route['score']:.2f}")
            print(f"  구간:")
            for seg in route['segments']:
                mode_name = {
                    'walk': '도보',
                    'bike': '따릉이',
                    'kickboard': '킥보드',
                    'transit': '대중교통'
                }.get(seg['mode'], seg['mode'])
                
                # 대중교통인 경우 노선 정보 포함
                if seg['mode'] == 'transit' and 'route_name' in seg:
                    # 노선 이름에서 버스/지하철 구분
                    route_name = seg['route_name']
                    if '호선' in route_name or '선' in route_name:
                        route_display = f"🚇 {route_name}"
                    else:
                        route_display = f"🚌 {route_name}번"
                    print(f"    - {route_display}: {seg.get('from', 'N/A')} → {seg.get('to', 'N/A')}")
                else:
                    print(f"    - {mode_name}: {seg.get('from', 'N/A')} → {seg.get('to', 'N/A')}")
            print()
    else:
        print("경로를 찾을 수 없습니다.")
    


if __name__ == "__main__":
    main()