#!/usr/bin/env python3
"""
강남구 Time-Expanded Multimodal RAPTOR v2.0
- 대중교통 + 공유 모빌리티 통합 경로 탐색
- OSM 기반 도보/모빌리티 경로 계산
- 파레토 최적화 및 사용자 선호도 반영
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
import heapq
import math
import logging
import sys

# PART1_2의 클래스 import (pickle 로드를 위해)
sys.path.append('.')
try:
    from PART1_2 import Stop, Route, Trip
except ImportError:
    # 클래스 정의 (pickle 로드용)
    @dataclass
    class Stop:
        stop_id: str
        stop_name: str
        stop_lat: float
        stop_lon: float
        stop_type: int = 0
        zone_id: str = 'gangnam'
    
    @dataclass  
    class Route:
        route_id: str
        route_short_name: str
        route_long_name: str
        route_type: int
        route_color: str = None
        n_trips: int = 0
    
    @dataclass
    class Trip:
        trip_id: str
        route_id: str
        service_id: str
        direction_id: int = 0
        stop_times: List = field(default_factory=list)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# 1. 데이터 구조 및 설정
# ============================================================================

class TransportMode(Enum):
    """교통 수단 타입"""
    WALK = "walk"
    BUS = "bus"
    SUBWAY = "subway"
    BIKE = "bike"           # 따릉이
    KICKBOARD = "kickboard" # 전동킥보드
    EBIKE = "ebike"         # 전기자전거

class JourneyType(Enum):
    """여정 타입"""
    TRANSIT_ONLY = "transit_only"      # 대중교통만
    MULTIMODAL = "multimodal"          # 멀티모달

@dataclass
class RoutePreference:
    """사용자 선호도 설정"""
    # 기본 가중치 (합이 1.0)
    time_weight: float = 0.4        # 시간 중요도
    transfer_weight: float = 0.3    # 환승 횟수 중요도  
    walk_weight: float = 0.2        # 도보 거리 중요도
    cost_weight: float = 0.1        # 비용 중요도
    
    # 멀티모달 선호도 (0.0~1.0, 높을수록 선호)
    mobility_preference: Dict[str, float] = field(default_factory=lambda: {
        'bike': 0.8,      # 따릉이 선호도
        'kickboard': 0.6, # 킥보드 선호도  
        'ebike': 0.7      # 전기자전거 선호도
    })
    
    # 제약 조건
    max_walk_distance: float = 1000  # 최대 도보 거리 (미터)
    max_total_time: float = 120      # 최대 총 소요시간 (분)
    max_transfers: int = 3           # 최대 환승 횟수

@dataclass
class MobilityOption:
    """모빌리티 옵션"""
    type: str                           # 'kickboard', 'ebike', 'bike'
    mobility_id: str                    # 차량/스테이션 ID
    coords: Tuple[float, float]         # 위치 좌표
    pickup_time: float                  # 도보로 도달 시간 (분)
    battery: float = 100.0              # 배터리 잔량 (%)
    must_return_to_station: bool = False # 따릉이 여부

@dataclass 
class JourneyState:
    """여정 중 상태"""
    has_mobility: bool = False
    mobility_type: Optional[str] = None
    mobility_id: Optional[str] = None
    pickup_location: Optional[Tuple[float, float]] = None
    battery_remaining: float = 100.0
    must_return_to_station: bool = False
    total_cost: float = 0.0
    
    def copy(self):
        return JourneyState(
            has_mobility=self.has_mobility,
            mobility_type=self.mobility_type,
            mobility_id=self.mobility_id,
            pickup_location=self.pickup_location,
            battery_remaining=self.battery_remaining,
            must_return_to_station=self.must_return_to_station,
            total_cost=self.total_cost
        )

@dataclass
class AccessOption:
    """출발지 접근 옵션"""
    stop_id: str
    stop_idx: int
    access_time: float                  # 접근 시간 (분)
    access_mode: TransportMode
    access_cost: float = 0.0
    initial_state: JourneyState = field(default_factory=JourneyState)

@dataclass
class Journey:
    """완성된 여정"""
    origin: Tuple[float, float]
    destination: Tuple[float, float]
    departure_time: int
    arrival_time: int
    total_time: float
    total_cost: float
    transfers: int
    total_walk_distance: float
    
    legs: List[Dict] = field(default_factory=list)
    used_mobility: List[str] = field(default_factory=list)
    
    def get_score(self, preference: RoutePreference) -> float:
        """선호도 기반 점수 계산 (낮을수록 좋음)"""
        score = (
            self.total_time * preference.time_weight +
            self.transfers * 10 * preference.transfer_weight +  # 환승당 10분 페널티
            self.total_walk_distance / 80 * preference.walk_weight +  # 도보속도 80m/분
            self.total_cost / 1000 * preference.cost_weight  # 비용 정규화
        )
        
        # 모빌리티 사용 시 선호도 보너스
        if self.used_mobility:
            mobility_bonus = 1.0
            for mobility in set(self.used_mobility):
                if mobility in preference.mobility_preference:
                    # 선호도가 높으면 점수 감소 (보너스)
                    mobility_bonus *= preference.mobility_preference[mobility]
            score *= mobility_bonus
            
        return score

# ============================================================================
# 2. 설정 상수
# ============================================================================

# 교통수단별 속도 (km/h)
SPEEDS = {
    TransportMode.WALK: 4.8,
    TransportMode.BIKE: 15.0,
    TransportMode.KICKBOARD: 20.0,
    TransportMode.EBIKE: 25.0
}

# 교통수단별 비용 (원)
COSTS = {
    TransportMode.WALK: 0,
    TransportMode.BUS: 1370,
    TransportMode.SUBWAY: 1370,
    TransportMode.BIKE: 1000,        # 따릉이 기본요금
    TransportMode.KICKBOARD: 390,    # 기본요금 + 분당요금
    TransportMode.EBIKE: 490
}

# 모빌리티별 최대 이용거리 (km)
MAX_MOBILITY_DISTANCE = {
    'bike': 10.0,
    'kickboard': 15.0, 
    'ebike': 20.0
}

# 배터리 소모율 (%/km)
BATTERY_CONSUMPTION = {
    'kickboard': 8.0,
    'ebike': 5.0
}

# 알고리즘 설정
MAX_ROUNDS = 6
INF = float('inf')

# ============================================================================
# 3. 메인 클래스
# ============================================================================

class TimeExpandedMultimodalRAPTOR:
    """Time-Expanded Multimodal RAPTOR 경로 탐색기"""
    
    def __init__(self, data_path: str = "gangnam_raptor_data"):
        """초기화"""
        print("🚀 Time-Expanded Multimodal RAPTOR 초기화...")
        
        # 데이터 로드
        self.raptor_data = self._load_raptor_data(data_path)
        self.road_network = self._load_road_network()
        
        # 성능 최적화용 캐시
        self._mobility_reachable_cache = {}
        self._road_distance_cache = {}
        
        # RAPTOR 데이터 추출
        self.stops = self.raptor_data['stops']
        self.routes = self.raptor_data['routes'] 
        self.trips = self.raptor_data['trips']
        self.timetables = self.raptor_data['timetables']
        self.transfers = self.raptor_data['transfers']
        self.stop_routes = self.raptor_data['stop_routes']
        self.route_stop_sequences = self.raptor_data['route_stop_sequences']
        self.stop_index_map = self.raptor_data['stop_index_map']
        self.index_to_stop = self.raptor_data['index_to_stop']
        
        # 모빌리티 데이터
        self.bike_stations = self.raptor_data.get('bike_stations', {})
        self.shared_vehicles = self.raptor_data.get('shared_vehicles', [])
        
        print(f"   ✅ 정류장: {len(self.stops):,}개")
        print(f"   ✅ 노선: {len(self.routes):,}개") 
        print(f"   ✅ 환승: {sum(len(t) for t in self.transfers.values()):,}개")
        print(f"   ✅ 따릉이: {len(self.bike_stations):,}개")
        print(f"   ✅ 공유차량: {len(self.shared_vehicles):,}개")
        
    def _load_raptor_data(self, data_path: str) -> Dict:
        """RAPTOR 데이터 로드"""
        try:
            with open(f"{data_path}/raptor_data.pkl", 'rb') as f:
                data = pickle.load(f)
            return data
        except Exception as e:
            raise Exception(f"RAPTOR 데이터 로드 실패: {e}")
    
    def _load_road_network(self) -> nx.Graph:
        """도로 네트워크 로드"""
        try:
            # pickle 파일 우선 시도
            try:
                with open("gangnam_road_network.pkl", 'rb') as f:
                    return pickle.load(f)
            except:
                # GraphML 파일 시도
                return nx.read_graphml("gangnam_road_network.graphml")
        except Exception as e:
            logger.warning(f"도로 네트워크 로드 실패: {e}")
            return None
    
    # ========================================================================
    # 4. 접근점 탐색
    # ========================================================================
    
    def find_access_options(self, origin: Tuple[float, float], 
                          journey_type: JourneyType,
                          preference: RoutePreference) -> List[AccessOption]:
        """출발지에서 접근 가능한 옵션들 탐색"""
        
        print(f"\n🎯 접근점 탐색 (모드: {journey_type.value})")
        access_options = []
        
        if journey_type == JourneyType.TRANSIT_ONLY:
            # 대중교통만: 도보로 갈 수 있는 정류장만
            walking_options = self._find_walking_access(origin, preference.max_walk_distance)
            access_options.extend(walking_options)
            
        else:  # MULTIMODAL
            # 1. 도보 접근 정류장
            walking_options = self._find_walking_access(origin, preference.max_walk_distance)
            access_options.extend(walking_options)
            
            # 2. 모빌리티 접근 정류장 (더 넓은 범위)
            mobility_options = self._find_mobility_access(origin, preference)
            access_options.extend(mobility_options)
        
        # 접근점이 너무 많으면 성능 저하 - 거리순 정렬 후 상위 N개만
        access_options.sort(key=lambda x: x.access_time)
        limited_options = access_options[:30]  # 최대 30개로 제한
        
        print(f"   ✅ 총 {len(limited_options)}개 접근점 발견 (전체 {len(access_options)}개 중)")
        return limited_options
    
    def _find_walking_access(self, origin: Tuple[float, float], 
                           max_distance: float) -> List[AccessOption]:
        """도보로 접근 가능한 정류장 탐색"""
        options = []
        
        for stop_id, stop in self.stops.items():
            distance = self._calculate_walk_distance(origin, (stop.stop_lat, stop.stop_lon))
            
            if distance <= max_distance:
                walk_time = distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)  # 분 변환
                
                if stop_id in self.stop_index_map:
                    options.append(AccessOption(
                        stop_id=stop_id,
                        stop_idx=self.stop_index_map[stop_id],
                        access_time=walk_time,
                        access_mode=TransportMode.WALK,
                        access_cost=0.0,
                        initial_state=JourneyState()
                    ))
        
        return sorted(options, key=lambda x: x.access_time)[:20]  # 상위 20개만
    
    def _find_mobility_access(self, origin: Tuple[float, float],
                            preference: RoutePreference) -> List[AccessOption]:
        """모빌리티로 접근 가능한 정류장 탐색"""
        options = []
        
        # 1. 근처 공유 모빌리티 찾기
        nearby_mobility = self._find_nearby_mobility(origin, 500)  # 500m 내
        
        for mobility in nearby_mobility:
            # 모빌리티까지 도보 시간
            pickup_distance = self._calculate_distance(origin, mobility.coords)
            pickup_time = pickup_distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)
            
            # 이 모빌리티로 갈 수 있는 정류장들
            reachable_stops = self._compute_mobility_reachable_stops(
                mobility.coords, mobility.type, mobility.battery
            )
            
            for stop_id, travel_time, cost in reachable_stops:
                if stop_id in self.stop_index_map:
                    total_time = pickup_time + travel_time
                    
                    initial_state = JourneyState(
                        has_mobility=True,
                        mobility_type=mobility.type,
                        mobility_id=mobility.mobility_id,
                        pickup_location=mobility.coords,
                        battery_remaining=mobility.battery - (travel_time * BATTERY_CONSUMPTION.get(mobility.type, 0)),
                        must_return_to_station=mobility.must_return_to_station,
                        total_cost=cost
                    )
                    
                    options.append(AccessOption(
                        stop_id=stop_id,
                        stop_idx=self.stop_index_map[stop_id],
                        access_time=total_time,
                        access_mode=TransportMode(mobility.type),
                        access_cost=cost,
                        initial_state=initial_state
                    ))
        
        # 2. 따릉이 스테이션 접근
        bike_options = self._find_bike_station_access(origin)
        options.extend(bike_options)
        
        return sorted(options, key=lambda x: x.access_time)[:50]  # 상위 50개만
    
    def _find_nearby_mobility(self, location: Tuple[float, float], 
                            radius: float) -> List[MobilityOption]:
        """근처 공유 모빌리티 찾기"""
        nearby = []
        
        for vehicle in self.shared_vehicles:
            distance = self._calculate_distance(
                location, (vehicle['lat'], vehicle['lon'])
            )
            
            if distance <= radius:
                pickup_time = distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)
                
                nearby.append(MobilityOption(
                    type=vehicle['type'],
                    mobility_id=vehicle['id'],
                    coords=(vehicle['lat'], vehicle['lon']),
                    pickup_time=pickup_time,
                    battery=vehicle.get('battery', 100),
                    must_return_to_station=False
                ))
        
        return nearby
    
    def _find_bike_station_access(self, origin: Tuple[float, float]) -> List[AccessOption]:
        """따릉이 스테이션 접근 옵션"""
        options = []
        
        for station_id, station in self.bike_stations.items():
            # 스테이션까지 도보 거리
            distance = self._calculate_distance(
                origin, (station['lat'], station['lon'])
            )
            
            if distance <= 800:  # 800m 내 스테이션만
                walk_time = distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)
                
                # 이 스테이션에서 갈 수 있는 정류장들
                bike_reachable = self._compute_bike_reachable_stops(
                    (station['lat'], station['lon'])
                )
                
                for stop_id, travel_time, cost in bike_reachable:
                    if stop_id in self.stop_index_map:
                        total_time = walk_time + 1 + travel_time  # +1분은 대여시간
                        
                        initial_state = JourneyState(
                            has_mobility=True,
                            mobility_type='bike',
                            mobility_id=station_id,
                            pickup_location=(station['lat'], station['lon']),
                            must_return_to_station=True,
                            total_cost=cost
                        )
                        
                        options.append(AccessOption(
                            stop_id=stop_id,
                            stop_idx=self.stop_index_map[stop_id],
                            access_time=total_time,
                            access_mode=TransportMode.BIKE,
                            access_cost=cost,
                            initial_state=initial_state
                        ))
        
        return options
    
    def _compute_mobility_reachable_stops(self, from_coords: Tuple[float, float],
                                        mobility_type: str, battery: float) -> List[Tuple[str, float, float]]:
        """모빌리티로 도달 가능한 정류장들 계산"""
        # 캐시 키 생성
        cache_key = (round(from_coords[0], 4), round(from_coords[1], 4), mobility_type, int(battery))
        if cache_key in self._mobility_reachable_cache:
            return self._mobility_reachable_cache[cache_key]
        
        reachable = []
        
        max_distance = min(
            battery / 100 * MAX_MOBILITY_DISTANCE[mobility_type] * 1000,  # 배터리 제한
            MAX_MOBILITY_DISTANCE[mobility_type] * 1000  # 절대 제한
        )
        
        # 성능 최적화: 거리순으로 정렬하여 상위 N개만 반환
        candidates = []
        
        for stop_id, stop in self.stops.items():
            stop_coords = (stop.stop_lat, stop.stop_lon)
            
            # 1차 필터링: 직선거리로 빠르게 필터링
            straight_distance = self._calculate_distance(from_coords, stop_coords)
            if straight_distance > max_distance or straight_distance <= 0:
                continue
            
            # 2차 계산: 도로망 거리 (필요시)
            if self.road_network and straight_distance <= max_distance * 0.7:  # 70% 이내만 정확히 계산
                distance = self._calculate_road_distance(from_coords, stop_coords)
            else:
                distance = straight_distance * 1.3  # 도로 거리 근사
            
            if distance <= max_distance:
                travel_time = distance / (SPEEDS[TransportMode(mobility_type)] * 1000 / 60)
                cost = COSTS[TransportMode(mobility_type)]
                
                candidates.append((stop_id, travel_time, cost, distance))
        
        # 거리순 정렬 후 상위 50개만 반환
        candidates.sort(key=lambda x: x[3])
        reachable = [(sid, tt, c) for sid, tt, c, _ in candidates[:50]]
        
        # 캐시 저장 (최대 1000개까지)
        if len(self._mobility_reachable_cache) < 1000:
            self._mobility_reachable_cache[cache_key] = reachable
        
        return reachable
    
    def _compute_bike_reachable_stops(self, station_coords: Tuple[float, float]) -> List[Tuple[str, float, float]]:
        """따릉이로 도달 가능한 정류장들 계산"""
        reachable = []
        max_distance = MAX_MOBILITY_DISTANCE['bike'] * 1000
        
        for stop_id, stop in self.stops.items():
            stop_coords = (stop.stop_lat, stop.stop_lon)
            
            if self.road_network:
                distance = self._calculate_road_distance(station_coords, stop_coords)
            else:
                distance = self._calculate_distance(station_coords, stop_coords)
            
            if distance <= max_distance and distance > 0:
                travel_time = distance / (SPEEDS[TransportMode.BIKE] * 1000 / 60)
                cost = COSTS[TransportMode.BIKE]
                
                reachable.append((stop_id, travel_time, cost))
        
        return reachable
    
    # ========================================================================
    # 5. Time-Expanded RAPTOR 알고리즘
    # ========================================================================
    
    def find_routes(self, origin: Tuple[float, float], destination: Tuple[float, float],
                   departure_time: str, journey_type: JourneyType,
                   preference: RoutePreference = None) -> List[Journey]:
        """경로 탐색 메인 함수"""
        
        if preference is None:
            preference = RoutePreference()
        
        print(f"\n🚀 경로 탐색 시작")
        print(f"   출발: {origin}")
        print(f"   도착: {destination}")  
        print(f"   출발시간: {departure_time}")
        print(f"   모드: {journey_type.value}")
        
        # 출발시간을 분 단위로 변환
        dep_minutes = self._time_to_minutes(departure_time)
        
        # 1. 접근점 탐색
        access_options = self.find_access_options(origin, journey_type, preference)
        if not access_options:
            print("❌ 접근 가능한 정류장을 찾을 수 없습니다")
            return []
        
        # 2. Time-Expanded RAPTOR 실행
        journeys = self._run_time_expanded_raptor(
            access_options, destination, dep_minutes, journey_type, preference
        )
        
        # 3. 파레토 최적화
        optimized_journeys = self._pareto_optimize(journeys, preference)
        
        print(f"\n✅ 총 {len(optimized_journeys)}개 최적 경로 발견")
        return optimized_journeys
    
    def _run_time_expanded_raptor(self, access_options: List[AccessOption],
                                destination: Tuple[float, float], departure_time: int,
                                journey_type: JourneyType, preference: RoutePreference) -> List[Journey]:
        """Time-Expanded RAPTOR 알고리즘 실행"""
        
        print(f"\n⚡ Time-Expanded RAPTOR 실행...")
        
        n_stops = len(self.stops)
        
        # tau[k][stop] = 라운드 k에서 stop에 도착하는 최소 시간
        tau = [[INF] * n_stops for _ in range(MAX_ROUNDS + 1)]
        
        # journey_states[k][stop] = 라운드 k에서 stop에서의 여정 상태
        journey_states = [{} for _ in range(MAX_ROUNDS + 1)]
        
        # parent 추적 (경로 재구성용)
        parent = [{} for _ in range(MAX_ROUNDS + 1)]
        
        # 1. 초기화: 접근점들 설정
        initial_stops = []
        for option in access_options:
            arrival_time = departure_time + option.access_time
            stop_idx = option.stop_idx
            
            tau[0][stop_idx] = arrival_time
            journey_states[0][stop_idx] = option.initial_state.copy()
            parent[0][stop_idx] = {
                'type': 'access',
                'access_option': option,
                'departure_time': departure_time
            }
            initial_stops.append((option.stop_id, arrival_time))
        
        print(f"   초기 접근 정류장: {len(initial_stops)}개")
        for stop_id, arr_time in initial_stops[:5]:  # 처음 5개만 출력
            stop = self.stops[stop_id]
            arr_time_int = int(arr_time)
            print(f"      - {stop.stop_name}: {arr_time_int//60:02d}:{arr_time_int%60:02d} 도착")
        
        # 2. RAPTOR 라운드 진행
        # 멀티모달은 탐색 공간이 크므로 라운드 수 제한
        max_rounds_for_type = 3 if journey_type == JourneyType.MULTIMODAL else MAX_ROUNDS
        
        for k in range(1, max_rounds_for_type + 1):
            print(f"   라운드 {k} 시작...")
            marked_stops = set()
            
            # 2-1. 대중교통 기반 전파
            route_marked = self._route_based_propagation(k, tau, journey_states, parent)
            marked_stops.update(route_marked)
            print(f"      대중교통 전파: {len(route_marked)}개 정류장 업데이트")
            
            # 2-2. 모빌리티 기반 전파 (멀티모달인 경우)
            if journey_type == JourneyType.MULTIMODAL and k <= 2:  # 라운드 2까지만 모빌리티 전파
                mobility_marked = self._mobility_based_propagation(k, tau, journey_states, parent)
                marked_stops.update(mobility_marked)
                print(f"      모빌리티 전파: {len(mobility_marked)}개 정류장 업데이트")
            
            # 2-3. 환승 전파 (도보 + 모빌리티)
            transfer_before = sum(1 for i in range(len(tau[k])) if tau[k][i] < INF)
            self._transfer_propagation_expanded(k, tau, journey_states, parent, journey_type)
            transfer_after = sum(1 for i in range(len(tau[k])) if tau[k][i] < INF)
            print(f"      환승 전파: {transfer_after - transfer_before}개 정류장 추가")
            
            total_reachable = sum(1 for i in range(len(tau[k])) if tau[k][i] < INF)
            print(f"      라운드 {k} 총 도달 가능: {total_reachable}개 정류장")
            
            if not marked_stops:
                print(f"   라운드 {k}에서 더 이상 개선 없음, 종료")
                break
        
        # 3. 목적지로의 경로 수집
        journeys = self._collect_destination_journeys(destination, tau, journey_states, parent, preference)
        
        return journeys
    
    def _route_based_propagation(self, k: int, tau: List[List[float]], 
                               journey_states: List[Dict], parent: List[Dict]) -> Set[int]:
        """대중교통 노선 기반 전파 - RAPTOR 표준 알고리즘"""
        marked = set()
        routes_to_scan = set()
        
        # 1단계: k-1 라운드에 도달한 정류장에서 탑승 가능한 노선들 수집
        for stop_idx in range(len(tau[k-1])):
            if tau[k-1][stop_idx] < INF:
                stop_id = self.index_to_stop.get(stop_idx)
                if stop_id:
                    # 이 정류장을 지나는 노선들 추가
                    for route_id in self.timetables.keys():
                        stop_sequence = self.route_stop_sequences.get(route_id, [])
                        if stop_id in stop_sequence:
                            routes_to_scan.add(route_id)
        
        # 2단계: 각 노선별로 처리
        for route_id in routes_to_scan:
            timetable = self.timetables.get(route_id)
            stop_sequence = self.route_stop_sequences.get(route_id, [])
            
            if not timetable or len(stop_sequence) < 2:
                continue
            
            # 시간표가 정상적인 구조인지 확인
            if not isinstance(timetable[0], list):
                continue
            
            # 이 노선의 각 trip별로 처리
            n_trips = len(timetable[0]) if timetable else 0
            
            for trip_idx in range(n_trips):
                # 이 trip에서 탑승할 정류장 찾기
                board_stop_idx = -1
                board_time = INF
                
                for i, stop_id in enumerate(stop_sequence):
                    if stop_id not in self.stop_index_map:
                        continue
                    
                    stop_idx = self.stop_index_map[stop_id]
                    arrival_time = tau[k-1][stop_idx]
                    
                    if arrival_time < INF and i < len(timetable):
                        if trip_idx < len(timetable[i]):
                            dep_time = timetable[i][trip_idx]
                            
                            # 도착시간 이후에 출발하는 경우만 탑승 가능
                            if dep_time >= arrival_time:
                                board_stop_idx = i
                                board_time = dep_time
                                break
                
                # 탑승 가능하면 이후 정류장들 업데이트
                if board_stop_idx >= 0:
                    board_stop_id = stop_sequence[board_stop_idx]
                    
                    # 하차 가능한 정류장들 업데이트
                    for j in range(board_stop_idx + 1, len(stop_sequence)):
                        alight_stop_id = stop_sequence[j]
                        if alight_stop_id not in self.stop_index_map:
                            continue
                        
                        alight_stop_idx = self.stop_index_map[alight_stop_id]
                        
                        # 같은 trip의 도착 시간
                        if j < len(timetable) and trip_idx < len(timetable[j]):
                            alight_time = timetable[j][trip_idx]
                            
                            # 시간 유효성 검사: 도착시간이 탑승시간보다 늦어야 함
                            if alight_time < board_time:
                                continue  # 잘못된 데이터 건너뛰기
                            
                            # 개선된 경우만 업데이트
                            if alight_time < tau[k][alight_stop_idx]:
                                tau[k][alight_stop_idx] = alight_time
                                
                                # 여정 상태 복사
                                board_state = journey_states[k-1].get(
                                    self.stop_index_map[board_stop_id], JourneyState()
                                )
                                journey_states[k][alight_stop_idx] = board_state.copy()
                                
                                # 대중교통 비용 추가 (첫 탑승 시에만)
                                # board_state가 이미 같은 노선을 타고 있었는지 확인
                                prev_parent = parent[k-1].get(self.stop_index_map[board_stop_id], {})
                                if prev_parent.get('type') != 'route' or prev_parent.get('route_id') != route_id:
                                    # 새로운 노선에 탑승하는 경우만 비용 추가
                                    route = self.routes.get(route_id)
                                    if route and route.route_type == 1:  # 지하철
                                        journey_states[k][alight_stop_idx].total_cost += COSTS[TransportMode.SUBWAY]
                                    else:  # 버스
                                        journey_states[k][alight_stop_idx].total_cost += COSTS[TransportMode.BUS]
                                
                                parent[k][alight_stop_idx] = {
                                    'type': 'route',
                                    'route_id': route_id,
                                    'board_stop': board_stop_id,
                                    'alight_stop': alight_stop_id,
                                    'board_time': board_time,
                                    'alight_time': alight_time,
                                    'from_round': k-1,
                                    'from_stop': self.stop_index_map[board_stop_id]
                                }
                                
                                marked.add(alight_stop_idx)
        
        return marked
    
    def _mobility_based_propagation(self, k: int, tau: List[List[float]],
                                  journey_states: List[Dict], parent: List[Dict]) -> Set[int]:
        """모빌리티 기반 전파"""
        marked = set()
        
        # k-1 라운드에서 도착한 정류장들을 확인
        for stop_idx in range(len(tau[k-1])):
            if tau[k-1][stop_idx] == INF:
                continue
            
            current_time = tau[k-1][stop_idx]
            current_state = journey_states[k-1].get(stop_idx, JourneyState())
            stop_id = self.index_to_stop.get(stop_idx)
            
            if not stop_id or stop_id not in self.stops:
                continue
            
            stop = self.stops[stop_id]
            stop_coords = (stop.stop_lat, stop.stop_lon)
            
            # 이 정류장에서 사용 가능한 모빌리티 옵션들
            mobility_options = self._get_mobility_options_at_stop(stop_coords, current_state)
            
            if mobility_options and k == 1:  # 첫 라운드에서만 상세 출력
                print(f"         정류장 {stop.stop_name}에서 {len(mobility_options)}개 모빌리티 옵션 발견")
            
            for option in mobility_options:
                # 이 모빌리티로 갈 수 있는 정류장들
                reachable = self._compute_mobility_reachable_stops(
                    stop_coords, option.type, option.battery
                )
                
                # 라운드가 높을수록 모빌리티 사용에 페널티 추가 (환승 줄이기)
                round_penalty = (k - 1) * 3  # 라운드당 3분 페널티
                
                for target_stop_id, travel_time, cost in reachable[:5]:  # 상위 5개로 제한 (성능 최적화)
                    if target_stop_id not in self.stop_index_map:
                        continue
                    
                    target_stop_idx = self.stop_index_map[target_stop_id]
                    arrival_time = current_time + option.pickup_time + travel_time + round_penalty
                    
                    if arrival_time < tau[k][target_stop_idx]:
                        tau[k][target_stop_idx] = arrival_time
                        
                        # 새로운 모빌리티 상태
                        new_state = current_state.copy()
                        new_state.has_mobility = True
                        new_state.mobility_type = option.type
                        new_state.mobility_id = option.mobility_id
                        new_state.pickup_location = option.coords
                        new_state.battery_remaining = option.battery - (travel_time * BATTERY_CONSUMPTION.get(option.type, 0))
                        new_state.must_return_to_station = option.must_return_to_station
                        new_state.total_cost += cost
                        
                        journey_states[k][target_stop_idx] = new_state
                        
                        parent[k][target_stop_idx] = {
                            'type': 'mobility',
                            'mobility_type': option.type,
                            'mobility_id': option.mobility_id,
                            'from_stop': stop_id,
                            'to_stop': target_stop_id,
                            'pickup_time': option.pickup_time,
                            'travel_time': travel_time,
                            'from_round': k-1,
                            'from_stop_idx': stop_idx
                        }
                        
                        marked.add(target_stop_idx)
        
        return marked
    
    def _get_mobility_options_at_stop(self, stop_coords: Tuple[float, float],
                                    current_state: JourneyState) -> List[MobilityOption]:
        """정류장에서 사용 가능한 모빌리티 옵션들"""
        options = []
        
        # 1. 현재 모빌리티를 계속 사용
        if current_state.has_mobility and current_state.battery_remaining > 20:
            options.append(MobilityOption(
                type=current_state.mobility_type,
                mobility_id=current_state.mobility_id,
                coords=stop_coords,  # 현재 위치
                pickup_time=0,  # 이미 탑승 중
                battery=current_state.battery_remaining,
                must_return_to_station=current_state.must_return_to_station
            ))
        
        # 2. 새로운 모빌리티 픽업 (현재 모빌리티가 없거나 반납 후)
        if not current_state.has_mobility or self._can_drop_mobility(stop_coords, current_state):
            nearby_mobility = self._find_nearby_mobility(stop_coords, 300)
            options.extend(nearby_mobility)
            
            # 따릉이 스테이션 체크
            nearby_bike_stations = self._find_nearby_bike_stations(stop_coords, 200)
            for station in nearby_bike_stations:
                options.append(MobilityOption(
                    type='bike',
                    mobility_id=station['id'],
                    coords=(station['lat'], station['lon']),
                    pickup_time=station['walk_time'] + 1,  # +1분 대여시간
                    battery=100,
                    must_return_to_station=True
                ))
        
        return options
    
    def _can_drop_mobility(self, location: Tuple[float, float], state: JourneyState) -> bool:
        """모빌리티 반납 가능 여부"""
        if not state.has_mobility:
            return False
        
        if state.mobility_type == 'bike':
            # 따릉이는 스테이션에서만 반납 가능
            nearby_stations = self._find_nearby_bike_stations(location, 100)
            return len(nearby_stations) > 0
        else:
            # 킥보드/전기자전거는 도로에서 반납 가능 (간단히 True로)
            return True
    
    def _find_nearby_bike_stations(self, location: Tuple[float, float], 
                                 radius: float) -> List[Dict]:
        """근처 따릉이 스테이션 찾기"""
        nearby = []
        
        for station_id, station in self.bike_stations.items():
            distance = self._calculate_distance(
                location, (station['lat'], station['lon'])
            )
            
            if distance <= radius:
                walk_time = distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)
                nearby.append({
                    'id': station_id,
                    'lat': station['lat'],
                    'lon': station['lon'],
                    'walk_time': walk_time
                })
        
        return nearby
    
    def _transfer_propagation_expanded(self, k: int, tau: List[List[float]],
                                     journey_states: List[Dict], parent: List[Dict],
                                     journey_type: JourneyType):
        """확장된 환승 전파 (도보 + 모빌리티)"""
        
        for stop_idx in range(len(tau[k])):
            if tau[k][stop_idx] == INF:
                continue
            
            stop_id = self.index_to_stop.get(stop_idx)
            if not stop_id or stop_id not in self.transfers:
                continue
            
            current_time = tau[k][stop_idx]
            current_state = journey_states[k].get(stop_idx, JourneyState())
            
            # 기존 도보 환승
            for transfer_stop_id, transfer_time in self.transfers[stop_id]:
                if transfer_stop_id not in self.stop_index_map:
                    continue
                
                transfer_idx = self.stop_index_map[transfer_stop_id]
                arrival_time = current_time + transfer_time
                
                if arrival_time < tau[k][transfer_idx]:
                    tau[k][transfer_idx] = arrival_time
                    journey_states[k][transfer_idx] = current_state.copy()
                    
                    parent[k][transfer_idx] = {
                        'type': 'transfer',
                        'transfer_type': 'walk',
                        'from_stop': stop_id,
                        'to_stop': transfer_stop_id,
                        'transfer_time': transfer_time,
                        'from_round': k,
                        'from_stop_idx': stop_idx
                    }
            
            # 멀티모달인 경우 추가 환승 옵션
            if journey_type == JourneyType.MULTIMODAL:
                self._add_mobility_transfers(k, stop_idx, stop_id, current_time, 
                                           current_state, tau, journey_states, parent)
    
    def _add_mobility_transfers(self, k: int, stop_idx: int, stop_id: str,
                              current_time: float, current_state: JourneyState,
                              tau: List[List[float]], journey_states: List[Dict], 
                              parent: List[Dict]):
        """모빌리티 기반 환승 추가"""
        
        stop = self.stops[stop_id]
        stop_coords = (stop.stop_lat, stop.stop_lon)
        
        # 현재 정류장에서 모빌리티로 갈 수 있는 정류장들
        mobility_options = self._get_mobility_options_at_stop(stop_coords, current_state)
        
        for option in mobility_options:
            reachable = self._compute_mobility_reachable_stops(
                stop_coords, option.type, option.battery
            )
            
            for target_stop_id, travel_time, cost in reachable[:5]:  # 상위 5개만 (성능 최적화)
                if target_stop_id not in self.stop_index_map or target_stop_id == stop_id:
                    continue
                
                target_idx = self.stop_index_map[target_stop_id]
                arrival_time = current_time + option.pickup_time + travel_time
                
                if arrival_time < tau[k][target_idx]:
                    tau[k][target_idx] = arrival_time
                    
                    new_state = current_state.copy()
                    new_state.has_mobility = True
                    new_state.mobility_type = option.type
                    new_state.mobility_id = option.mobility_id
                    new_state.total_cost += cost
                    
                    journey_states[k][target_idx] = new_state
                    
                    parent[k][target_idx] = {
                        'type': 'mobility_transfer',
                        'mobility_type': option.type,
                        'from_stop': stop_id,
                        'to_stop': target_stop_id,
                        'pickup_time': option.pickup_time,
                        'travel_time': travel_time,
                        'from_round': k,
                        'from_stop_idx': stop_idx
                    }
    
    # ========================================================================
    # 6. 목적지 도달 및 경로 재구성
    # ========================================================================
    
    def _collect_destination_journeys(self, destination: Tuple[float, float],
                                    tau: List[List[float]], journey_states: List[Dict],
                                    parent: List[Dict], preference: RoutePreference) -> List[Journey]:
        """목적지로 도달하는 모든 경로 수집"""
        
        print(f"\n🎯 목적지 도달 경로 수집...")
        journeys = []
        
        # 목적지 근처 정류장들 찾기
        destination_stops = self._find_destination_stops(destination, preference.max_walk_distance)
        print(f"   목적지 근처 정류장: {len(destination_stops)}개")
        for stop_id, egress_time, mode in destination_stops[:5]:
            stop = self.stops[stop_id]
            print(f"      - {stop.stop_name}: {egress_time:.1f}분 도보")
        
        # 출발 시간 가져오기 (첫 번째 접근점의 출발시간)
        departure_time = INF
        for stop_idx in range(len(tau[0])):
            if stop_idx in parent[0] and parent[0][stop_idx]['type'] == 'access':
                departure_time = min(departure_time, parent[0][stop_idx]['departure_time'])
        
        # 각 라운드에서 도달 가능한 정류장 확인
        found_paths = 0
        for k in range(MAX_ROUNDS + 1):
            round_paths = 0
            for dest_stop_id, egress_time, egress_mode in destination_stops:
                if dest_stop_id not in self.stop_index_map:
                    continue
                
                stop_idx = self.stop_index_map[dest_stop_id]
                if tau[k][stop_idx] < INF:
                    arrival_time = tau[k][stop_idx]
                    
                    # 출발시간보다 이른 도착시간은 무시 (전날 데이터)
                    if arrival_time < departure_time:
                        continue
                    
                    round_paths += 1
                    arrival_time_int = int(arrival_time)
                    stop = self.stops[dest_stop_id]
                    print(f"      {stop.stop_name}: {arrival_time_int//60:02d}:{arrival_time_int%60:02d} 도착")
                    
                    # 경로 재구성
                    journey = self._reconstruct_journey(
                        destination, k, stop_idx, arrival_time,
                        egress_time, egress_mode, journey_states[k].get(stop_idx, JourneyState()),
                        parent
                    )
                    
                    if journey and journey.departure_time >= departure_time:
                        journeys.append(journey)
                        found_paths += 1
                    else:
                        print(f"         경로 재구성 실패 또는 유효하지 않은 시간")
            
            if round_paths > 0:
                print(f"   라운드 {k}: {round_paths}개 목적지 정류장 도달 가능")
        
        print(f"   ✅ {found_paths}개 경로 발견")
        
        # 대중교통 구간이 같은 경로는 제거 (가장 짧은 도보 거리만 유지)
        unique_transit_journeys = {}
        for journey in journeys:
            # 대중교통 구간만 추출
            transit_key = []
            for leg in journey.legs:
                if leg['type'] == 'transit':
                    transit_key.append((leg['from'], leg['to'], leg.get('route_name', '')))
            
            transit_tuple = tuple(transit_key)
            
            # 처음 보는 대중교통 경로이거나 더 짧은 도보 거리인 경우만 저장
            if transit_tuple not in unique_transit_journeys or \
               journey.total_walk_distance < unique_transit_journeys[transit_tuple].total_walk_distance:
                unique_transit_journeys[transit_tuple] = journey
        
        return list(unique_transit_journeys.values())
    
    def _find_destination_stops(self, destination: Tuple[float, float], 
                              max_distance: float) -> List[Tuple[str, float, str]]:
        """목적지 근처 정류장들 찾기"""
        dest_stops = []
        
        for stop_id, stop in self.stops.items():
            distance = self._calculate_walk_distance(destination, (stop.stop_lat, stop.stop_lon))
            
            if distance <= max_distance:
                walk_time = distance / (SPEEDS[TransportMode.WALK] * 1000 / 60)
                dest_stops.append((stop_id, walk_time, 'walk'))
        
        return sorted(dest_stops, key=lambda x: x[1])[:20]  # 상위 20개
    
    def _reconstruct_journey(self, destination: Tuple[float, float], final_round: int,
                           final_stop_idx: int, arrival_time: float, egress_time: float,
                           egress_mode: str, final_state: JourneyState,
                           parent: List[Dict]) -> Optional[Journey]:
        """경로 재구성"""
        
        try:
            legs = []
            current_round = final_round
            current_stop_idx = final_stop_idx
            total_walk_distance = 0
            transfers = 0
            used_mobility = []
            last_route_id = None  # 이전 노선 추적
            
            # 도착 구간 추가
            final_stop_id = self.index_to_stop[final_stop_idx]
            final_stop = self.stops[final_stop_id]
            
            legs.append({
                'type': 'egress',
                'mode': egress_mode,
                'from': final_stop.stop_name,
                'to': 'destination',
                'departure_time': arrival_time,
                'arrival_time': arrival_time + egress_time,
                'duration': egress_time,
                'distance': egress_time * SPEEDS[TransportMode.WALK] * 1000 / 60
            })
            
            total_walk_distance += egress_time * SPEEDS[TransportMode.WALK] * 1000 / 60
            
            # 역방향으로 경로 추적
            while current_round >= 0 and current_stop_idx in parent[current_round]:
                p = parent[current_round][current_stop_idx]
                
                if p['type'] == 'access':
                    # 접근 구간
                    option = p['access_option']
                    legs.append({
                        'type': 'access',
                        'mode': option.access_mode.value,
                        'from': 'origin',
                        'to': self.stops[option.stop_id].stop_name,
                        'departure_time': p['departure_time'],
                        'arrival_time': p['departure_time'] + option.access_time,
                        'duration': option.access_time,
                        'cost': option.access_cost
                    })
                    
                    if option.access_mode == TransportMode.WALK:
                        total_walk_distance += option.access_time * SPEEDS[TransportMode.WALK] * 1000 / 60
                    else:
                        used_mobility.append(option.access_mode.value)
                    
                    break
                
                elif p['type'] == 'route':
                    # 대중교통 구간
                    route = self.routes[p['route_id']]
                    mode = 'subway' if route.route_type == 1 else 'bus'
                    
                    legs.append({
                        'type': 'transit',
                        'mode': mode,
                        'route_name': route.route_short_name,
                        'from': self.stops[p['board_stop']].stop_name,
                        'to': self.stops[p['alight_stop']].stop_name,
                        'departure_time': p['board_time'],
                        'arrival_time': p['alight_time'],
                        'duration': p['alight_time'] - p['board_time'],
                        'cost': COSTS[TransportMode.SUBWAY] if mode == 'subway' else COSTS[TransportMode.BUS]
                    })
                    
                    # 환승은 다른 노선으로 갈아탈 때만 카운트
                    if last_route_id is not None and last_route_id != p['route_id']:
                        transfers += 1
                    last_route_id = p['route_id']
                    
                    current_round = p['from_round']
                    current_stop_idx = p['from_stop']
                
                elif p['type'] in ['mobility', 'mobility_transfer']:
                    # 모빌리티 구간
                    legs.append({
                        'type': 'mobility',
                        'mode': p['mobility_type'],
                        'from': self.stops[p['from_stop']].stop_name,
                        'to': self.stops[p['to_stop']].stop_name,
                        'pickup_time': p['pickup_time'],
                        'travel_time': p['travel_time'],
                        'duration': p['pickup_time'] + p['travel_time'],
                        'cost': COSTS[TransportMode(p['mobility_type'])]
                    })
                    
                    used_mobility.append(p['mobility_type'])
                    
                    # 모빌리티 사용도 환승으로 카운트
                    if last_route_id is not None:
                        transfers += 1
                    last_route_id = None  # 모빌리티 구간 표시
                    
                    current_round = p['from_round']
                    current_stop_idx = p['from_stop_idx']
                
                elif p['type'] == 'transfer':
                    # 환승 구간
                    legs.append({
                        'type': 'transfer',
                        'mode': p['transfer_type'],
                        'from': self.stops[p['from_stop']].stop_name,
                        'to': self.stops[p['to_stop']].stop_name,
                        'duration': p['transfer_time'],
                        'cost': 0
                    })
                    
                    if p['transfer_type'] == 'walk':
                        total_walk_distance += p['transfer_time'] * SPEEDS[TransportMode.WALK] * 1000 / 60
                    
                    current_stop_idx = p['from_stop_idx']
                
                else:
                    break
            
            # 리스트 뒤집기 (시간 순서대로)
            legs.reverse()
            
            # 같은 노선의 연속된 구간 합치기
            merged_legs = []
            current_transit_leg = None
            
            for leg in legs:
                if leg['type'] == 'transit' and current_transit_leg and \
                   current_transit_leg['type'] == 'transit' and \
                   current_transit_leg.get('route_name') == leg.get('route_name'):
                    # 같은 노선이면 도착지와 시간만 업데이트
                    current_transit_leg['to'] = leg['to']
                    current_transit_leg['arrival_time'] = leg['arrival_time']
                    current_transit_leg['duration'] = current_transit_leg['arrival_time'] - current_transit_leg['departure_time']
                else:
                    # 다른 노선이거나 대중교통이 아니면 새로운 leg
                    if current_transit_leg:
                        merged_legs.append(current_transit_leg)
                    current_transit_leg = leg if leg['type'] == 'transit' else None
                    if leg['type'] != 'transit':
                        merged_legs.append(leg)
            
            # 마지막 transit leg 추가
            if current_transit_leg:
                merged_legs.append(current_transit_leg)
            
            legs = merged_legs
            
            # Journey 객체 생성
            total_time = arrival_time + egress_time - legs[0]['departure_time'] if legs else 0
            # 비용은 final_state에 이미 정확히 계산되어 있음
            total_cost = final_state.total_cost
            
            return Journey(
                origin=destination,  # 임시
                destination=destination,
                departure_time=legs[0]['departure_time'] if legs else 0,
                arrival_time=arrival_time + egress_time,
                total_time=total_time,
                total_cost=total_cost,
                transfers=max(0, transfers - 1),  # 첫 번째 탑승은 환승이 아님
                total_walk_distance=total_walk_distance,
                legs=legs,
                used_mobility=used_mobility
            )
            
        except Exception as e:
            logger.error(f"경로 재구성 오류: {e}")
            return None
    
    # ========================================================================
    # 7. 파레토 최적화
    # ========================================================================
    
    def _pareto_optimize(self, journeys: List[Journey], 
                        preference: RoutePreference) -> List[Journey]:
        """파레토 최적화 및 선호도 기반 정렬"""
        
        if not journeys:
            return []
        
        print(f"\n🎯 최적 경로 선택 ({len(journeys)}개 → ", end="")
        
        # 1. 중복 제거 및 기본 필터링
        unique_journeys = {}
        for journey in journeys:
            if (journey.total_time <= preference.max_total_time and
                journey.transfers <= preference.max_transfers and
                journey.total_walk_distance <= preference.max_walk_distance):
                
                # 경로 키 생성 (주요 경유 정류장 포함)
                main_stops = []
                for leg in journey.legs:
                    if leg['type'] == 'transit':
                        main_stops.append((leg['from'], leg['to'], leg.get('route_name', '')))
                
                # 출발/도착 시간을 분 단위로 반올림해서 미세한 차이는 무시
                journey_key = (
                    round(journey.departure_time),  # 분 단위 반올림
                    round(journey.arrival_time),    # 분 단위 반올림
                    journey.total_cost,
                    journey.transfers,
                    tuple(main_stops)
                )
                
                # 중복이 아니거나 더 나은 점수인 경우만 저장
                if journey_key not in unique_journeys or \
                   journey.get_score(preference) < unique_journeys[journey_key].get_score(preference):
                    unique_journeys[journey_key] = journey
        
        filtered = list(unique_journeys.values())
        
        if not filtered:
            print("0개) - 제약조건을 만족하는 경로 없음")
            return []
        
        # 2. 파레토 최적화
        pareto_optimal = []
        
        for i, journey1 in enumerate(filtered):
            is_dominated = False
            
            for j, journey2 in enumerate(filtered):
                if i != j:
                    # journey2가 journey1을 지배하는지 확인
                    if (journey2.total_time <= journey1.total_time and
                        journey2.transfers <= journey1.transfers and
                        journey2.total_walk_distance <= journey1.total_walk_distance and
                        journey2.total_cost <= journey1.total_cost):
                        
                        # 적어도 하나는 더 좋아야 함 (같으면 지배하지 않음)
                        if (journey2.total_time < journey1.total_time or
                            journey2.transfers < journey1.transfers or
                            journey2.total_walk_distance < journey1.total_walk_distance or
                            journey2.total_cost < journey1.total_cost):
                            is_dominated = True
                            break
            
            if not is_dominated:
                pareto_optimal.append(journey1)
        
        # 3. 선호도 기반 정렬 후 상위 5개 선택
        pareto_optimal.sort(key=lambda j: j.get_score(preference))
        
        # 파레토 최적이 너무 적으면 필터링된 전체에서 상위 5개 선택
        if len(pareto_optimal) < 5:
            print(f"{len(pareto_optimal)}개 파레토 최적 → 전체 중 상위 5개)")
            # 필터링된 전체를 점수순 정렬
            filtered.sort(key=lambda j: j.get_score(preference))
            # 중복 제거하면서 상위 5개 선택
            final_selection = []
            seen_keys = set()
            for journey in filtered:
                # 더 구체적인 키로 중복 체크 (주요 대중교통 구간 포함)
                transit_segments = []
                for leg in journey.legs:
                    if leg['type'] == 'transit':
                        transit_segments.append((leg['from'], leg['to'], leg.get('route_name', '')))
                
                key = (
                    round(journey.total_time), 
                    journey.transfers, 
                    journey.total_cost,
                    tuple(transit_segments)  # 주요 대중교통 구간 포함
                )
                
                if key not in seen_keys:
                    seen_keys.add(key)
                    final_selection.append(journey)
                if len(final_selection) >= 5:
                    break
            return final_selection
        else:
            print(f"{len(pareto_optimal)}개 파레토 최적 → 상위 5개)")
            return pareto_optimal[:5]
    
    # ========================================================================
    # 8. 유틸리티 함수들
    # ========================================================================
    
    def _time_to_minutes(self, time_str: str) -> int:
        """시간 문자열을 분으로 변환"""
        try:
            time_obj = datetime.strptime(time_str, "%H:%M")
            return time_obj.hour * 60 + time_obj.minute
        except:
            # 기본값: 오전 8시
            return 8 * 60
    
    def _calculate_distance(self, coord1: Tuple[float, float], 
                          coord2: Tuple[float, float]) -> float:
        """두 좌표 간 직선거리 계산 (미터)"""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        R = 6371000  # 지구 반지름 (미터)
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_lat/2)**2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * 
             math.sin(delta_lon/2)**2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _calculate_road_distance(self, coord1: Tuple[float, float],
                               coord2: Tuple[float, float]) -> float:
        """OSM 도로망 기반 최단거리 계산 (일단 직선거리로 근사)"""
        # OSM 계산이 너무 오래 걸리므로 일단 직선거리로 근사
        return self._calculate_distance(coord1, coord2) * 1.3  # 도로 우회 계수
    
    def _calculate_walk_distance(self, coord1: Tuple[float, float],
                               coord2: Tuple[float, float]) -> float:
        """도보 거리 계산 - 짧은 거리는 OSM 사용"""
        straight_distance = self._calculate_distance(coord1, coord2)
        
        # 300m 이내이고 OSM 네트워크가 있으면 실제 경로 계산
        if straight_distance <= 300 and self.road_network:
            # 캐시 확인
            cache_key = (round(coord1[0], 5), round(coord1[1], 5), 
                        round(coord2[0], 5), round(coord2[1], 5))
            if cache_key in self._road_distance_cache:
                return self._road_distance_cache[cache_key]
            
            try:
                # 가장 가까운 노드 찾기
                node1 = self._find_nearest_node(coord1)
                node2 = self._find_nearest_node(coord2)
                
                if node1 and node2 and node1 != node2:
                    # 최단 경로 계산
                    path_length = nx.shortest_path_length(self.road_network, 
                                                        node1, node2, weight='length')
                    
                    # 캐시 저장
                    if len(self._road_distance_cache) < 5000:
                        self._road_distance_cache[cache_key] = path_length
                    
                    return path_length
            except:
                pass
        
        # 실패시 또는 먼 거리는 근사값
        return straight_distance * 1.2  # 도보는 1.2 계수 (더 직선적)
    
    def _find_nearest_node(self, coord: Tuple[float, float]) -> Optional[Any]:
        """가장 가까운 도로 네트워크 노드 찾기"""
        if not self.road_network:
            return None
        
        # 캐시 확인
        cache_key = (round(coord[0], 5), round(coord[1], 5))
        if hasattr(self, '_nearest_node_cache'):
            if cache_key in self._nearest_node_cache:
                return self._nearest_node_cache[cache_key]
        else:
            self._nearest_node_cache = {}
        
        min_distance = INF
        nearest_node = None
        
        for node, data in self.road_network.nodes(data=True):
            if 'y' in data and 'x' in data:
                node_coord = (data['y'], data['x'])
                distance = self._calculate_distance(coord, node_coord)
                
                if distance < min_distance:
                    min_distance = distance
                    nearest_node = node
                
                # 10m 이내면 충분히 가까움
                if distance < 10:
                    break
        
        # 캐시 저장
        if len(self._nearest_node_cache) < 10000:
            self._nearest_node_cache[cache_key] = nearest_node
        
        return nearest_node
    
    def print_journey(self, journey: Journey, preference: RoutePreference):
        """여정 정보 출력"""
        print(f"\n{'='*80}")
        print(f"🎯 여정 정보 (점수: {journey.get_score(preference):.2f})")
        print(f"{'='*80}")
        dep_time_int = int(journey.departure_time)
        arr_time_int = int(journey.arrival_time)
        print(f"📍 출발시간: {dep_time_int//60:02d}:{dep_time_int%60:02d}")
        print(f"📍 도착시간: {arr_time_int//60:02d}:{arr_time_int%60:02d}")
        print(f"⏰ 총 소요시간: {journey.total_time:.1f}분")
        print(f"💰 총 비용: {journey.total_cost:,.0f}원")
        print(f"🔄 환승 횟수: {journey.transfers}회")
        print(f"🚶 도보 거리: {journey.total_walk_distance:.0f}m")
        if journey.used_mobility:
            print(f"🛴 사용 모빌리티: {', '.join(set(journey.used_mobility))}")
        
        print(f"\n📋 상세 경로:")
        for i, leg in enumerate(journey.legs, 1):
            mode_emoji = {
                'walk': '🚶', 'bus': '🚌', 'subway': '🚇',
                'bike': '🚲', 'kickboard': '🛴', 'ebike': '🚴'
            }
            
            emoji = mode_emoji.get(leg['mode'], '🔸')
            
            if leg['type'] == 'access':
                print(f"   {i}. {emoji} {leg['from']} → {leg['to']} ({leg['duration']:.1f}분)")
            elif leg['type'] == 'transit':
                print(f"   {i}. {emoji} {leg['route_name']}: {leg['from']} → {leg['to']} ({leg['duration']:.1f}분)")
            elif leg['type'] == 'mobility':
                print(f"   {i}. {emoji} {leg['from']} → {leg['to']} ({leg['duration']:.1f}분)")
            elif leg['type'] == 'transfer':
                print(f"   {i}. {emoji} 환승: {leg['from']} → {leg['to']} ({leg['duration']:.1f}분)")
            elif leg['type'] == 'egress':
                print(f"   {i}. {emoji} {leg['from']} → {leg['to']} ({leg['duration']:.1f}분)")

# ============================================================================
# 9. 메인 실행
# ============================================================================

def main():
    """메인 실행 함수"""
    print("🚀 Time-Expanded Multimodal RAPTOR 시작")
    
    try:
        # 시스템 초기화
        raptor = TimeExpandedMultimodalRAPTOR()
        
        # 예시 경로 탐색
        print(f"\n" + "="*80)
        print("📍 예시 경로 탐색")
        print("="*80)
        
        # 출발지/목적지 설정 (양재역 → 수서역) - 3호선 테스트
        origin = (37.4846, 127.0342)      # 양재역 (3호선)
        destination = (37.4871, 127.1006) # 수서역 근처 (더 먼 거리)
        departure_time = "14:00"  # 12시로 변경
        
        # 사용자 선호도 설정
        preference = RoutePreference(
            time_weight=0.4,
            transfer_weight=0.3,
            walk_weight=0.2,
            cost_weight=0.1,
            mobility_preference={
                'bike': 0.8,
                'kickboard': 0.7,
                'ebike': 0.6
            }
        )
        
        # 1. 대중교통만 경로 탐색
        print(f"\n🚇 대중교통 전용 경로 탐색")
        transit_journeys = raptor.find_routes(
            origin, destination, departure_time,
            JourneyType.TRANSIT_ONLY, preference
        )
        
        if transit_journeys:
            print(f"\n✅ 최적 대중교통 경로 ({len(transit_journeys)}개):")
            for i, journey in enumerate(transit_journeys[:3], 1):
                print(f"\n[경로 {i}]")
                raptor.print_journey(journey, preference)
        
        # 2. 멀티모달 경로 탐색
        print(f"\n🛴 멀티모달 경로 탐색")
        multimodal_journeys = raptor.find_routes(
            origin, destination, departure_time,
            JourneyType.MULTIMODAL, preference
        )
        
        if multimodal_journeys:
            print(f"\n✅ 최적 멀티모달 경로:")
            raptor.print_journey(multimodal_journeys[0], preference)
        
        # 3. 결과 비교
        if transit_journeys and multimodal_journeys:
            print(f"\n📊 경로 비교:")
            print(f"   대중교통: {transit_journeys[0].total_time:.1f}분, {transit_journeys[0].total_cost:.0f}원")
            print(f"   멀티모달: {multimodal_journeys[0].total_time:.1f}분, {multimodal_journeys[0].total_cost:.0f}원")
            
            time_saved = transit_journeys[0].total_time - multimodal_journeys[0].total_time
            cost_diff = multimodal_journeys[0].total_cost - transit_journeys[0].total_cost
            
            if time_saved > 0:
                print(f"   ⏰ 멀티모달이 {time_saved:.1f}분 단축")
            if cost_diff != 0:
                print(f"   💰 비용 차이: {cost_diff:+.0f}원")
        
        print(f"\n🎉 경로 탐색 완료!")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)