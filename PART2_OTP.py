#!/usr/bin/env python3
"""
OTP 스타일 멀티모달 RAPTOR
- 기존 PART2_NEW.py의 대중교통 RAPTOR 활용
- 공유 모빌리티를 가상 정거장으로 통합
- 모든 정거장을 동일하게 처리
"""

import pickle
import json
import numpy as np
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque
from enum import Enum
import logging
import time
import pandas as pd
from pathlib import Path

# 기존 클래스들 import
try:
    from PART1_2 import Stop, Route, Trip
    from PART2_NEW import TransportMode, JourneyType, RoutePreference
    from virtual_stop_generator import VirtualStop, VirtualRoute
    from gbfs_updater import GBFSUpdater
except ImportError as e:
    print(f"Import error: {e}")
    exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@dataclass
class OTPJourney:
    """OTP 스타일 여정"""
    legs: List[Dict] = field(default_factory=list)
    total_time: float = 0.0
    total_cost: int = 0
    n_transfers: int = 0
    walk_distance: float = 0.0
    
    # 파레토 최적화를 위한 점수
    time_score: float = 0.0
    transfer_score: float = 0.0
    walk_score: float = 0.0
    cost_score: float = 0.0
    total_score: float = 0.0

class OTPStyleMultimodalRAPTOR:
    """OTP 스타일 통합 RAPTOR"""
    
    def __init__(self, data_dir: str = 'gangnam_raptor_data',
                 virtual_stations_dir: str = 'grid_virtual_stations'):
        """초기화"""
        self.data_dir = Path(data_dir)
        self.virtual_stations_dir = Path(virtual_stations_dir)
        
        # 통합 데이터 구조
        self.all_stops: Dict[str, Stop] = {}
        self.all_routes: Dict[str, Route] = {}
        self.all_trips: Dict[str, Trip] = {}
        self.all_timetables: Dict[str, List[List[int]]] = {}
        self.all_route_stops: Dict[str, List[str]] = {}
        self.all_transfers: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        
        # 가상 정거장 관련
        self.virtual_stops: Dict[str, Dict] = {}
        self.virtual_routes: Dict[str, Dict] = {}
        self.kickboard_locations: Dict[str, Dict] = {}
        self.bike_stations: Dict[str, Dict] = {}
        
        # 초기화
        self._load_all_data()
        self._build_integrated_network()
        
        logger.info(f"OTP RAPTOR 초기화 완료: "
                   f"{len(self.all_stops)} 정거장, "
                   f"{len(self.all_routes)} 노선")
    
    def _load_all_data(self):
        """모든 데이터 로드"""
        # 1. 기존 대중교통 데이터 로드
        raptor_file = self.data_dir / 'raptor_data.pkl'
        if not raptor_file.exists():
            raise FileNotFoundError("RAPTOR 데이터 없음. PART1_2.py 먼저 실행")
            
        with open(raptor_file, 'rb') as f:
            data = pickle.load(f)
        
        # 대중교통 데이터 복사
        self.all_stops = data['stops'].copy()
        self.all_routes = data['routes'].copy()
        self.all_trips = data['trips'].copy()
        self.all_timetables = data['timetables'].copy()
        self.all_route_stops = data['route_stop_sequences'].copy()
        
        # 환승 데이터
        for stop_id, transfers in data['transfers'].items():
            self.all_transfers[stop_id] = transfers
        
        logger.info(f"대중교통 데이터 로드: "
                   f"{len(self.all_stops)} 정류장, "
                   f"{len(self.all_routes)} 노선")
        
        # 2. 따릉이 대여소 데이터 로드
        self._load_ttareungee_stations(data)
        
        # 3. 킥보드 가상 정거장 데이터 로드
        self._load_virtual_stations()
    
    def _load_virtual_stations(self):
        """가상 정거장 데이터 로드"""
        # 500대 시나리오 사용
        stations_file = self.virtual_stations_dir / 'virtual_stations_500.csv'
        kickboards_file = self.virtual_stations_dir / 'kickboards_500.csv'
        
        if not stations_file.exists():
            logger.warning("가상 정거장 데이터 없음. 생성 필요")
            return
        
        # 가상 정거장 로드
        stations_df = pd.read_csv(stations_file)
        kickboards_df = pd.read_csv(kickboards_file)
        
        # 가상 정거장을 Stop으로 변환
        for _, row in stations_df.iterrows():
            virtual_stop = Stop(
                stop_id=row['station_id'],
                stop_name=row['station_name'],
                stop_lat=row['center_lat'],
                stop_lon=row['center_lon'],
                stop_type=6,  # 킥보드 정거장
                zone_id='gangnam'
            )
            self.all_stops[virtual_stop.stop_id] = virtual_stop
            self.virtual_stops[virtual_stop.stop_id] = {
                'n_kickboards': row['n_kickboards'],
                'demand': row.get('demand', 0)
            }
        
        # 킥보드 위치 정보
        for _, row in kickboards_df.iterrows():
            self.kickboard_locations[row['vehicle_id']] = {
                'station_id': row['station_id'],
                'lat': row['lat'],
                'lon': row['lon'],
                'battery': row['battery']
            }
        
        # 가상 노선 생성
        self._create_virtual_routes()
        
        logger.info(f"가상 정거장 로드: "
                   f"{len(self.virtual_stops)} 정거장, "
                   f"{len(self.kickboard_locations)} 킥보드")
    
    def _load_ttareungee_stations(self, raptor_data):
        """따릉이 대여소 로드 및 가상 정거장 변환"""
        bike_stations = raptor_data.get('bike_stations', {})
        
        if not bike_stations:
            logger.warning("따릉이 대여소 데이터 없음")
            return
        
        # 따릉이 대여소를 OTP 정거장으로 변환
        self.bike_stations = {}
        for station_id, station_info in bike_stations.items():
            # 정거장 ID 생성 (기존 Stop ID와 충돌 방지)
            otp_stop_id = f"BIKE_{station_id}"
            
            # Stop 객체 생성
            bike_stop = Stop(
                stop_id=otp_stop_id,
                stop_name=f"따릉이_{station_id}",
                stop_lat=station_info['lat'],
                stop_lon=station_info['lon'],
                stop_type=2,  # 2 = 따릉이
                zone_id='gangnam'
            )
            
            # 전체 정거장에 추가
            self.all_stops[otp_stop_id] = bike_stop
            self.bike_stations[otp_stop_id] = {
                'original_id': station_id,
                'lat': station_info['lat'],
                'lon': station_info['lon'],
                'capacity': 15,  # 평균 거치대 수
                'available_bikes': 8  # 평균 이용 가능 대수
            }
        
        logger.info(f"따릉이 대여소 로드: {len(self.bike_stations)}개")
        
        # 따릉이 대여소 간 가상 노선 생성
        self._create_bike_routes()
    
    def _create_bike_routes(self):
        """따릉이 대여소 간 가상 노선 생성"""
        bike_stations = list(self.bike_stations.keys())
        route_id = 5000  # 따릉이 노선 ID는 5000번대
        
        # 가까운 대여소끼리 연결 (최대 3km - 따릉이는 더 먼 거리 가능)
        for i, from_station in enumerate(bike_stations):
            from_stop = self.all_stops[from_station]
            
            # 성능을 위해 근처 대여소만 확인
            nearby_stations = []
            for to_station in bike_stations:
                if from_station != to_station:
                    to_stop = self.all_stops[to_station]
                    dist = self._haversine_distance(
                        from_stop.stop_lat, from_stop.stop_lon,
                        to_stop.stop_lat, to_stop.stop_lon
                    )
                    if dist <= 3000:  # 3km 이내
                        nearby_stations.append((to_station, dist))
            
            # 거리순 정렬 후 상위 5개만 연결
            nearby_stations.sort(key=lambda x: x[1])
            for to_station, dist in nearby_stations[:5]:
                route_key = f"VR_BIKE_{route_id:04d}"
                
                # Route 생성
                route = Route(
                    route_id=route_key,
                    route_short_name=f"따릉이{route_id}",
                    route_long_name=f"{from_stop.stop_name}→{self.all_stops[to_station].stop_name}",
                    route_type=12,  # 12 = 따릉이 노선
                    n_trips=1
                )
                self.all_routes[route_key] = route
                
                # 정류장 순서
                self.all_route_stops[route_key] = [from_station, to_station]
                
                # 시간표 (15km/h 평균 속도)
                travel_time = int(dist / 1000 / 15 * 60)  # 분
                departure_times = []
                
                # 5분 간격으로 운행 (06:00 ~ 23:00)
                for hour in range(6, 23):
                    for minute in range(0, 60, 5):
                        dep_time = hour * 60 + minute
                        departure_times.append(dep_time)
                
                # 시간표 생성 [출발역 시간, 도착역 시간]
                self.all_timetables[route_key] = [
                    departure_times,  # 출발역
                    [t + travel_time for t in departure_times]  # 도착역
                ]
                
                route_id += 1
        
        logger.info(f"따릉이 가상 노선 생성: {route_id-5000}개")
    
    def _create_virtual_routes(self):
        """가상 정거장 간 노선 생성"""
        route_id = 1
        virtual_stations = list(self.virtual_stops.keys())
        
        # 가까운 정거장끼리 연결 (최대 2km)
        for i, from_station in enumerate(virtual_stations):
            from_stop = self.all_stops[from_station]
            
            for to_station in virtual_stations[i+1:]:
                to_stop = self.all_stops[to_station]
                
                # 거리 계산
                dist = self._haversine_distance(
                    from_stop.stop_lat, from_stop.stop_lon,
                    to_stop.stop_lat, to_stop.stop_lon
                )
                
                if dist <= 2000:  # 2km 이내
                    # 양방향 가상 노선 생성
                    for direction, (start, end) in enumerate([
                        (from_station, to_station),
                        (to_station, from_station)
                    ]):
                        route_key = f"VR_KICK_{route_id:04d}"
                        
                        # Route 생성
                        route = Route(
                            route_id=route_key,
                            route_short_name=f"킥보드{route_id}",
                            route_long_name=f"{self.all_stops[start].stop_name}→"
                                          f"{self.all_stops[end].stop_name}",
                            route_type=11,  # 킥보드 노선
                            n_trips=1
                        )
                        self.all_routes[route_key] = route
                        
                        # 정류장 순서
                        self.all_route_stops[route_key] = [start, end]
                        
                        # 시간표 (20km/h 속도)
                        travel_time = int(dist / 1000 / 20 * 60)  # 분
                        departure_times = []
                        
                        # 5분 간격으로 운행 (06:00 ~ 23:00)
                        for hour in range(6, 23):
                            for minute in range(0, 60, 5):
                                dep_time = hour * 60 + minute
                                departure_times.append(dep_time)
                        
                        # 시간표 생성 [출발역 시간, 도착역 시간]
                        self.all_timetables[route_key] = [
                            departure_times,  # 출발역
                            [t + travel_time for t in departure_times]  # 도착역
                        ]
                        
                        route_id += 1
        
        logger.info(f"가상 노선 생성: {route_id-1}개")
    
    def _build_integrated_network(self):
        """통합 네트워크 구축"""
        # 1. 대중교통 ↔ 가상 정거장 환승 생성
        self._create_intermodal_transfers()
        
        # 2. 정거장 인덱스 생성
        self.stop_idx_to_id = {i: stop_id for i, stop_id in enumerate(self.all_stops.keys())}
        self.stop_id_to_idx = {stop_id: i for i, stop_id in enumerate(self.all_stops.keys())}
        
        logger.info(f"통합 네트워크 구축 완료: {len(self.all_transfers)} 환승")
    
    def _create_intermodal_transfers(self):
        """대중교통 ↔ 가상 정거장 환승 생성"""
        max_walk_dist = 300  # 300m
        
        # 각 가상 정거장에서 가까운 대중교통 정류장 연결
        for v_stop_id in self.virtual_stops.keys():
            v_stop = self.all_stops[v_stop_id]
            
            # 주변 대중교통 정류장 검색
            for t_stop_id, t_stop in self.all_stops.items():
                # 대중교통 정류장만 (type 0-4)
                if t_stop.stop_type >= 5:
                    continue
                
                dist = self._haversine_distance(
                    v_stop.stop_lat, v_stop.stop_lon,
                    t_stop.stop_lat, t_stop.stop_lon
                )
                
                if dist <= max_walk_dist:
                    walk_time = int(dist / 1.33)  # 80m/분
                    
                    # 양방향 환승
                    self.all_transfers[v_stop_id].append((t_stop_id, walk_time))
                    self.all_transfers[t_stop_id].append((v_stop_id, walk_time))
    
    def find_routes(self, origin: Tuple[float, float], 
                   destination: Tuple[float, float],
                   departure_time: str = "08:00",
                   preference: RoutePreference = None) -> List[OTPJourney]:
        """통합 경로 탐색"""
        if preference is None:
            preference = RoutePreference()
        
        start_time = time.time()
        
        # 시간 변환
        dep_minutes = self._time_to_minutes(departure_time)
        
        # 가까운 정류장 찾기 (대중교통 + 가상)
        origin_stops = self._find_nearest_stops(origin, preference.max_walk_distance)
        dest_stops = self._find_nearest_stops(destination, preference.max_walk_distance)
        
        if not origin_stops or not dest_stops:
            logger.warning("출발지/도착지 근처에 정류장 없음")
            return []
        
        # RAPTOR 실행
        journeys = self._run_integrated_raptor(
            origin_stops, dest_stops, dep_minutes, preference
        )
        
        # 경로 재구성
        result_journeys = []
        for journey_data in journeys:
            journey = self._reconstruct_journey(
                journey_data, origin, destination
            )
            if journey:
                result_journeys.append(journey)
        
        # 점수 계산 및 정렬
        self._calculate_scores(result_journeys, preference)
        result_journeys.sort(key=lambda j: j.total_score, reverse=True)
        
        elapsed = time.time() - start_time
        logger.info(f"경로 탐색 완료: {len(result_journeys)}개 경로, "
                   f"{elapsed:.2f}초")
        
        return result_journeys[:5]  # 상위 5개
    
    def _run_integrated_raptor(self, origin_stops: List[Tuple[str, float]], 
                              dest_stops: List[Tuple[str, float]],
                              dep_time: int,
                              preference: RoutePreference) -> List[Dict]:
        """통합 RAPTOR 알고리즘"""
        MAX_ROUNDS = preference.max_transfers + 1
        n_stops = len(self.all_stops)
        
        # 초기화
        tau = np.full(n_stops, np.inf)  # 최단 도착 시간
        tau_round = np.full((MAX_ROUNDS, n_stops), np.inf)
        
        # 부모 정보 (역추적용)
        parent = [None] * n_stops
        parent_round = [[None] * n_stops for _ in range(MAX_ROUNDS)]
        
        # 출발 정류장 초기화
        marked_stops = set()
        for stop_id, walk_time in origin_stops:
            if stop_id in self.stop_id_to_idx:
                idx = self.stop_id_to_idx[stop_id]
                arrival_time = dep_time + walk_time
                tau[idx] = arrival_time
                tau_round[0][idx] = arrival_time
                marked_stops.add(idx)
        
        # 라운드별 처리
        for round_k in range(MAX_ROUNDS):
            if not marked_stops:
                break
            
            # 새로운 마크된 정류장
            new_marked = set()
            
            # 모든 노선 스캔 (대중교통 + 가상)
            for route_id in self.all_routes.keys():
                earliest_trip = -1
                boarding_stop = -1
                
                # 노선의 정류장 순서대로
                stop_sequence = self.all_route_stops.get(route_id, [])
                if not stop_sequence:
                    continue
                
                timetable = self.all_timetables.get(route_id, [])
                if not timetable:
                    continue
                
                for seq, stop_id in enumerate(stop_sequence):
                    if stop_id not in self.stop_id_to_idx:
                        continue
                        
                    stop_idx = self.stop_id_to_idx[stop_id]
                    
                    # 탑승 가능 확인
                    if stop_idx in marked_stops and earliest_trip == -1:
                        if seq < len(timetable):
                            departures = timetable[seq]
                            # 다음 출발 찾기
                            trip_idx = self._find_next_departure(
                                departures, tau[stop_idx]
                            )
                            if trip_idx != -1:
                                earliest_trip = trip_idx
                                boarding_stop = stop_idx
                    
                    # 하차 및 개선
                    elif earliest_trip != -1 and seq < len(timetable):
                        # 시간표 인덱스 범위 확인
                        if earliest_trip < len(timetable[seq]):
                            arrival = timetable[seq][earliest_trip]
                            
                            if arrival < tau[stop_idx]:
                                tau[stop_idx] = arrival
                                tau_round[round_k][stop_idx] = arrival
                                parent[stop_idx] = (
                                    boarding_stop, route_id, earliest_trip
                                )
                                parent_round[round_k][stop_idx] = parent[stop_idx]
                                new_marked.add(stop_idx)
            
            # 환승 적용
            marked_stops = new_marked.copy()
            for stop_idx in new_marked:
                stop_id = self.stop_idx_to_id[stop_idx]
                
                for next_stop_id, transfer_time in self.all_transfers[stop_id]:
                    if next_stop_id not in self.stop_id_to_idx:
                        continue
                        
                    next_idx = self.stop_id_to_idx[next_stop_id]
                    new_arrival = tau[stop_idx] + transfer_time
                    
                    if new_arrival < tau[next_idx]:
                        tau[next_idx] = new_arrival
                        tau_round[round_k][next_idx] = new_arrival
                        parent[next_idx] = (stop_idx, 'walk', transfer_time)
                        parent_round[round_k][next_idx] = parent[next_idx]
                        marked_stops.add(next_idx)
        
        # 도착지별 최적 경로 수집
        journeys = []
        for dest_stop_id, walk_time in dest_stops:
            if dest_stop_id not in self.stop_id_to_idx:
                continue
                
            dest_idx = self.stop_id_to_idx[dest_stop_id]
            
            for round_k in range(MAX_ROUNDS):
                if tau_round[round_k][dest_idx] < np.inf:
                    journey = {
                        'round': round_k,
                        'arrival_time': tau_round[round_k][dest_idx] + walk_time,
                        'dest_stop': dest_stop_id,
                        'parent_info': parent_round[round_k]
                    }
                    journeys.append(journey)
        
        return journeys
    
    def _reconstruct_journey(self, journey_data: Dict, 
                           origin: Tuple[float, float],
                           destination: Tuple[float, float]) -> Optional[OTPJourney]:
        """여정 재구성"""
        dest_idx = self.stop_id_to_idx[journey_data['dest_stop']]
        parent_info = journey_data['parent_info']
        
        # 역추적
        path = []
        current_idx = dest_idx
        
        while current_idx is not None and parent_info[current_idx] is not None:
            parent_data = parent_info[current_idx]
            
            if parent_data[1] == 'walk':
                # 도보 구간
                from_idx = parent_data[0]
                path.append({
                    'type': 'walk',
                    'from_stop': self.stop_idx_to_id[from_idx],
                    'to_stop': self.stop_idx_to_id[current_idx],
                    'time': parent_data[2]
                })
                current_idx = from_idx
            else:
                # 대중교통/킥보드 구간
                boarding_idx, route_id, trip_idx = parent_data
                path.append({
                    'type': 'transit',
                    'route_id': route_id,
                    'boarding_stop': self.stop_idx_to_id[boarding_idx],
                    'alighting_stop': self.stop_idx_to_id[current_idx],
                    'trip_idx': trip_idx
                })
                current_idx = boarding_idx
        
        if not path:
            return None
        
        # 경로 뒤집기 및 leg 생성
        path.reverse()
        journey = OTPJourney()
        
        # 출발지 → 첫 정류장 도보
        first_stop_id = path[0].get('from_stop') or path[0].get('boarding_stop')
        first_stop = self.all_stops[first_stop_id]
        walk_dist = self._haversine_distance(
            origin[0], origin[1], first_stop.stop_lat, first_stop.stop_lon
        )
        journey.legs.append({
            'type': 'walk',
            'from': '출발지',
            'to': first_stop.stop_name,
            'duration': int(walk_dist / 1.33),
            'distance': walk_dist
        })
        journey.walk_distance += walk_dist
        
        # 중간 구간들
        for segment in path:
            if segment['type'] == 'walk':
                from_stop = self.all_stops[segment['from_stop']]
                to_stop = self.all_stops[segment['to_stop']]
                
                journey.legs.append({
                    'type': 'walk',
                    'from': from_stop.stop_name,
                    'to': to_stop.stop_name,
                    'duration': segment['time'],
                    'distance': segment['time'] * 1.33
                })
                journey.walk_distance += segment['time'] * 1.33
                
            else:  # transit
                route = self.all_routes[segment['route_id']]
                boarding_stop = self.all_stops[segment['boarding_stop']]
                alighting_stop = self.all_stops[segment['alighting_stop']]
                
                # 모드 판별
                duration = self._calculate_transit_time(
                    segment['route_id'], 
                    segment['boarding_stop'],
                    segment['alighting_stop'],
                    segment['trip_idx']
                )
                
                if route.route_type == 11:
                    mode = 'kickboard'
                    icon = '🛴'
                    # 킥보드 비용: 기본료 1000 + 분당 150
                    cost = 1000 + (duration * 150)
                elif route.route_type == 12:
                    mode = 'bike'
                    icon = '🚲'
                    # 따릉이 비용: 1시간 기본료 1000원
                    cost = 1000
                elif route.route_type == 1:
                    mode = 'subway'
                    icon = '🚇'
                    cost = 1400
                else:
                    mode = 'bus'
                    icon = '🚌'
                    cost = 1500
                
                journey.legs.append({
                    'type': 'transit',
                    'mode': mode,
                    'icon': icon,
                    'route_name': route.route_short_name,
                    'from': boarding_stop.stop_name,
                    'to': alighting_stop.stop_name,
                    'duration': duration
                })
                
                # 비용 추가 (환승 시 추가 요금 없음으로 간단화)
                if mode in ['kickboard', 'bike']:
                    # 킥보드와 따릉이는 각각 독립적으로 과금
                    journey.total_cost += cost
                elif mode in ['bus', 'subway'] and not any(
                    leg.get('mode') in ['bus', 'subway'] 
                    for leg in journey.legs[:-1]
                ):
                    # 대중교통은 첫 승차시만 과금
                    journey.total_cost += cost
        
        # 마지막 정류장 → 도착지 도보
        last_stop_id = path[-1].get('to_stop') or path[-1].get('alighting_stop')
        last_stop = self.all_stops[last_stop_id]
        walk_dist = self._haversine_distance(
            last_stop.stop_lat, last_stop.stop_lon, destination[0], destination[1]
        )
        journey.legs.append({
            'type': 'walk',
            'from': last_stop.stop_name,
            'to': '도착지',
            'duration': int(walk_dist / 1.33),
            'distance': walk_dist
        })
        journey.walk_distance += walk_dist
        
        # 총 소요시간 및 환승 계산
        journey.total_time = sum(leg['duration'] for leg in journey.legs)
        journey.n_transfers = len([leg for leg in journey.legs if leg['type'] == 'transit']) - 1
        
        return journey
    
    def _find_nearest_stops(self, location: Tuple[float, float], 
                           max_distance: float) -> List[Tuple[str, float]]:
        """가까운 정류장 찾기 (대중교통 + 가상)"""
        nearby = []
        
        for stop_id, stop in self.all_stops.items():
            dist = self._haversine_distance(
                location[0], location[1], stop.stop_lat, stop.stop_lon
            )
            
            if dist <= max_distance:
                walk_time = int(dist / 1.33)  # 80m/분
                nearby.append((stop_id, walk_time))
        
        # 거리순 정렬
        nearby.sort(key=lambda x: x[1])
        
        return nearby[:10]  # 최대 10개
    
    def _find_next_departure(self, departures: List[int], 
                           earliest_time: float) -> int:
        """다음 출발 시간 찾기 (이진 탐색)"""
        if not departures:
            return -1
            
        left, right = 0, len(departures) - 1
        result = -1
        
        while left <= right:
            mid = (left + right) // 2
            if departures[mid] >= earliest_time:
                result = mid
                right = mid - 1
            else:
                left = mid + 1
        
        return result
    
    def _calculate_transit_time(self, route_id: str, from_stop: str, 
                               to_stop: str, trip_idx: int) -> int:
        """대중교통 소요시간 계산"""
        stop_sequence = self.all_route_stops.get(route_id, [])
        timetable = self.all_timetables.get(route_id, [])
        
        if not stop_sequence or not timetable:
            return 0
        
        try:
            from_idx = stop_sequence.index(from_stop)
            to_idx = stop_sequence.index(to_stop)
            
            if from_idx < len(timetable) and to_idx < len(timetable):
                departure = timetable[from_idx][trip_idx]
                arrival = timetable[to_idx][trip_idx]
                return arrival - departure
        except (ValueError, IndexError):
            pass
        
        return 0
    
    def _calculate_scores(self, journeys: List[OTPJourney], 
                         preference: RoutePreference):
        """점수 계산"""
        if not journeys:
            return
        
        # 정규화를 위한 최대/최소값
        min_time = min(j.total_time for j in journeys)
        max_time = max(j.total_time for j in journeys)
        min_transfers = min(j.n_transfers for j in journeys)
        max_transfers = max(j.n_transfers for j in journeys)
        min_walk = min(j.walk_distance for j in journeys)
        max_walk = max(j.walk_distance for j in journeys)
        min_cost = min(j.total_cost for j in journeys)
        max_cost = max(j.total_cost for j in journeys)
        
        for journey in journeys:
            # 각 요소 점수 (0~1, 높을수록 좋음)
            if max_time > min_time:
                journey.time_score = 1 - (journey.total_time - min_time) / (max_time - min_time)
            else:
                journey.time_score = 1.0
            
            if max_transfers > min_transfers:
                journey.transfer_score = 1 - (journey.n_transfers - min_transfers) / (max_transfers - min_transfers)
            else:
                journey.transfer_score = 1.0
            
            if max_walk > min_walk:
                journey.walk_score = 1 - (journey.walk_distance - min_walk) / (max_walk - min_walk)
            else:
                journey.walk_score = 1.0
            
            if max_cost > min_cost:
                journey.cost_score = 1 - (journey.total_cost - min_cost) / (max_cost - min_cost)
            else:
                journey.cost_score = 1.0
            
            # 가중 평균
            journey.total_score = (
                preference.time_weight * journey.time_score +
                preference.transfer_weight * journey.transfer_score +
                preference.walk_weight * journey.walk_score +
                preference.cost_weight * journey.cost_score
            )
    
    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, 
                           lat2: float, lon2: float) -> float:
        """두 지점 간 거리 (미터)"""
        R = 6371000  # 지구 반경
        phi1, phi2 = np.radians(lat1), np.radians(lat2)
        delta_phi = np.radians(lat2 - lat1)
        delta_lambda = np.radians(lon2 - lon1)
        
        a = np.sin(delta_phi/2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda/2)**2
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
        
        return R * c
    
    @staticmethod
    def _time_to_minutes(time_str: str) -> int:
        """시간 문자열을 분으로 변환"""
        parts = time_str.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    
    def print_journey(self, journey: OTPJourney):
        """여정 출력"""
        print(f"\n총 소요시간: {journey.total_time:.0f}분")
        print(f"환승: {journey.n_transfers}회")
        print(f"도보: {journey.walk_distance:.0f}m")
        print(f"비용: {journey.total_cost:,}원")
        print(f"점수: {journey.total_score:.3f}")
        
        print("\n상세 경로:")
        for i, leg in enumerate(journey.legs):
            if leg['type'] == 'walk':
                print(f"  {i+1}. 🚶 {leg['from']} → {leg['to']} "
                      f"({leg['duration']}분, {leg['distance']:.0f}m)")
            else:
                print(f"  {i+1}. {leg['icon']} [{leg['route_name']}] "
                      f"{leg['from']} → {leg['to']} ({leg['duration']}분)")

# 테스트
if __name__ == "__main__":
    print("OTP 스타일 멀티모달 RAPTOR 초기화 중...")
    raptor = OTPStyleMultimodalRAPTOR()
    
    # 테스트 경로
    test_cases = [
        {
            'name': '강남역 → 선릉역',
            'origin': (37.4979, 127.0276),
            'destination': (37.5045, 127.0486),
            'time': '08:30'
        },
        {
            'name': '양재역 → 수서역',
            'origin': (37.4846, 127.0342),
            'destination': (37.4871, 127.1006),
            'time': '12:00'
        }
    ]
    
    for test in test_cases:
        print(f"\n{'='*60}")
        print(f"경로: {test['name']}")
        print(f"출발 시간: {test['time']}")
        print(f"{'='*60}")
        
        # 선호도 설정
        preference = RoutePreference(
            time_weight=0.4,
            transfer_weight=0.3,
            walk_weight=0.2,
            cost_weight=0.1,
            max_walk_distance=800
        )
        
        # 경로 탐색
        journeys = raptor.find_routes(
            test['origin'], test['destination'],
            test['time'], preference
        )
        
        # 결과 출력
        if journeys:
            print(f"\n{len(journeys)}개 경로 발견")
            for i, journey in enumerate(journeys[:3]):
                print(f"\n[경로 {i+1}]")
                raptor.print_journey(journey)
        else:
            print("경로를 찾을 수 없습니다.")