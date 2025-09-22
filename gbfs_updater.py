#!/usr/bin/env python3
"""
GBFS 데이터 수집기
- 따릉이: 정거장 기반 (station-based)
- 스윙: 자유 배치 (free-floating)
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import threading
import time
import logging
from collections import defaultdict

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class BikeStation:
    """따릉이 정거장 정보"""
    station_id: str
    name: str
    lat: float
    lon: float
    capacity: int
    bikes_available: int = 0
    docks_available: int = 0
    is_active: bool = True
    last_reported: datetime = field(default_factory=datetime.now)

@dataclass
class SharedVehicle:
    """공유 모빌리티 차량 정보"""
    vehicle_id: str
    lat: float
    lon: float
    battery: float
    provider: str
    is_available: bool = True
    last_reported: datetime = field(default_factory=datetime.now)

class GBFSUpdater:
    """GBFS 스타일 데이터 업데이터"""
    
    def __init__(self, config_path: str = 'config/gbfs_config.json'):
        """초기화"""
        self.config_path = Path(config_path)
        self.config = self._load_config()
        
        # 데이터 저장소
        self.bike_stations: Dict[str, BikeStation] = {}
        self.shared_vehicles: Dict[str, SharedVehicle] = {}
        
        # 캐싱
        self._last_update = {}
        self._update_lock = threading.Lock()
        self._running = False
        self._update_thread = None
        
        # 초기 데이터 로드
        self._initial_load()
    
    def _load_config(self) -> Dict:
        """설정 파일 로드"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _initial_load(self):
        """초기 데이터 로드"""
        logger.info("초기 데이터 로드 시작...")
        
        for provider_config in self.config['providers']:
            if provider_config['name'] == 'seoul_bike':
                self._load_bike_stations(provider_config)
            elif provider_config['name'] == 'swing':
                self._load_shared_vehicles(provider_config)
        
        logger.info(f"로드 완료: {len(self.bike_stations)} 따릉이 정거장, "
                   f"{len(self.shared_vehicles)} 스윙 킥보드")
    
    def _load_bike_stations(self, config: Dict):
        """따릉이 정거장 데이터 로드"""
        try:
            # 실제로는 API에서 가져와야 하지만, 현재는 CSV 파일 사용
            df = pd.read_csv(config['data_source'])
            
            for _, row in df.iterrows():
                station = BikeStation(
                    station_id=f"BIKE_{row['station_id']}",
                    name=row['station_name'],
                    lat=row['lat'],
                    lon=row['lon'],
                    capacity=int(row.get('capacity', 20)),
                    bikes_available=int(row.get('bikes_available', 
                                              np.random.randint(0, 15))),
                    docks_available=int(row.get('docks_available', 
                                              np.random.randint(0, 10)))
                )
                self.bike_stations[station.station_id] = station
                
        except FileNotFoundError:
            logger.warning("따릉이 데이터 파일 없음, 샘플 데이터 생성")
            self._generate_sample_bike_stations()
    
    def _generate_sample_bike_stations(self):
        """샘플 따릉이 정거장 생성 (강남구 주요 지점)"""
        sample_stations = [
            ("BIKE_001", "강남역 1번출구", 37.4979, 127.0276, 20),
            ("BIKE_002", "강남역 11번출구", 37.4983, 127.0286, 25),
            ("BIKE_003", "역삼역 3번출구", 37.5006, 127.0367, 15),
            ("BIKE_004", "선릉역 5번출구", 37.5045, 127.0486, 20),
            ("BIKE_005", "삼성역 4번출구", 37.5088, 127.0569, 20),
            ("BIKE_006", "논현역 7번출구", 37.5110, 127.0215, 15),
            ("BIKE_007", "신논현역 9번출구", 37.5048, 127.0247, 20),
            ("BIKE_008", "양재역 9번출구", 37.4846, 127.0342, 25),
            ("BIKE_009", "매봉역 1번출구", 37.4867, 127.0468, 15),
            ("BIKE_010", "도곡역 4번출구", 37.4910, 127.0553, 20),
        ]
        
        for station_id, name, lat, lon, capacity in sample_stations:
            bikes_available = np.random.randint(0, capacity)
            self.bike_stations[station_id] = BikeStation(
                station_id=station_id,
                name=name,
                lat=lat,
                lon=lon,
                capacity=capacity,
                bikes_available=bikes_available,
                docks_available=capacity - bikes_available
            )
    
    def _load_shared_vehicles(self, config: Dict):
        """스윙 킥보드 데이터 로드"""
        try:
            df = pd.read_csv(config['data_source'])
            
            for idx, row in df.iterrows():
                vehicle = SharedVehicle(
                    vehicle_id=f"KICK_{idx:04d}",
                    lat=row['lat'],
                    lon=row['lon'],
                    battery=row['battery'],
                    provider=row['provider'],
                    is_available=row.get('available', True)
                )
                self.shared_vehicles[vehicle.vehicle_id] = vehicle
                
        except Exception as e:
            logger.error(f"스윙 데이터 로드 실패: {e}")
    
    def get_current_data(self) -> Dict[str, Any]:
        """현재 데이터 반환 (thread-safe)"""
        with self._update_lock:
            return {
                'timestamp': datetime.now().isoformat(),
                'bike_stations': {
                    k: {
                        'station_id': v.station_id,
                        'name': v.name,
                        'lat': v.lat,
                        'lon': v.lon,
                        'bikes_available': v.bikes_available,
                        'docks_available': v.docks_available,
                        'is_active': v.is_active
                    } for k, v in self.bike_stations.items()
                },
                'shared_vehicles': {
                    k: {
                        'vehicle_id': v.vehicle_id,
                        'lat': v.lat,
                        'lon': v.lon,
                        'battery': v.battery,
                        'provider': v.provider,
                        'is_available': v.is_available
                    } for k, v in self.shared_vehicles.items()
                    if v.is_available and v.battery > 20  # 배터리 20% 이상만
                }
            }
    
    def start(self):
        """백그라운드 업데이트 시작"""
        if self._running:
            logger.warning("이미 실행 중")
            return
        
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop)
        self._update_thread.daemon = True
        self._update_thread.start()
        logger.info("GBFS 업데이터 시작")
    
    def stop(self):
        """업데이트 중지"""
        self._running = False
        if self._update_thread:
            self._update_thread.join()
        logger.info("GBFS 업데이터 중지")
    
    def _update_loop(self):
        """주기적 업데이트 루프"""
        while self._running:
            try:
                self._simulate_updates()
                time.sleep(60)  # 1분마다 업데이트
            except Exception as e:
                logger.error(f"업데이트 오류: {e}")
                time.sleep(5)
    
    def _simulate_updates(self):
        """실시간 데이터 시뮬레이션 (실제로는 API 호출)"""
        with self._update_lock:
            # 따릉이 정거장 상태 업데이트
            for station in self.bike_stations.values():
                # 랜덤하게 자전거 수 변경
                change = np.random.randint(-2, 3)
                station.bikes_available = max(0, 
                    min(station.capacity, station.bikes_available + change))
                station.docks_available = station.capacity - station.bikes_available
                station.last_reported = datetime.now()
            
            # 스윙 킥보드 위치/상태 업데이트
            for vehicle in self.shared_vehicles.values():
                # 10% 확률로 사용 상태 변경
                if np.random.random() < 0.1:
                    vehicle.is_available = not vehicle.is_available
                
                # 사용 중이면 위치 이동 (약간)
                if not vehicle.is_available:
                    vehicle.lat += np.random.uniform(-0.001, 0.001)
                    vehicle.lon += np.random.uniform(-0.001, 0.001)
                    vehicle.battery -= np.random.uniform(0, 2)
                    vehicle.battery = max(0, vehicle.battery)
                
                vehicle.last_reported = datetime.now()
    
    def get_stations_near(self, lat: float, lon: float, 
                         radius_m: float = 500) -> List[BikeStation]:
        """주변 따릉이 정거장 검색"""
        nearby = []
        
        for station in self.bike_stations.values():
            dist = self._haversine_distance(lat, lon, station.lat, station.lon)
            if dist <= radius_m and station.is_active and station.bikes_available > 0:
                nearby.append(station)
        
        return sorted(nearby, key=lambda s: 
                     self._haversine_distance(lat, lon, s.lat, s.lon))
    
    def get_vehicles_near(self, lat: float, lon: float, 
                         radius_m: float = 300) -> List[SharedVehicle]:
        """주변 킥보드 검색"""
        nearby = []
        
        for vehicle in self.shared_vehicles.values():
            if not vehicle.is_available or vehicle.battery < 20:
                continue
            
            dist = self._haversine_distance(lat, lon, vehicle.lat, vehicle.lon)
            if dist <= radius_m:
                nearby.append(vehicle)
        
        return sorted(nearby, key=lambda v: 
                     self._haversine_distance(lat, lon, v.lat, v.lon))
    
    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, 
                           lat2: float, lon2: float) -> float:
        """두 지점 간 거리 계산 (미터)"""
        R = 6371000  # 지구 반경 (미터)
        phi1, phi2 = np.radians(lat1), np.radians(lat2)
        delta_phi = np.radians(lat2 - lat1)
        delta_lambda = np.radians(lon2 - lon1)
        
        a = (np.sin(delta_phi/2)**2 + 
             np.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda/2)**2)
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
        
        return R * c

# 테스트 코드
if __name__ == "__main__":
    updater = GBFSUpdater()
    
    # 현재 데이터 확인
    data = updater.get_current_data()
    print(f"따릉이 정거장: {len(data['bike_stations'])}개")
    print(f"스윙 킥보드: {len(data['shared_vehicles'])}개")
    
    # 강남역 주변 검색 테스트
    gangnam_lat, gangnam_lon = 37.4979, 127.0276
    
    nearby_stations = updater.get_stations_near(gangnam_lat, gangnam_lon, 500)
    print(f"\n강남역 500m 내 따릉이: {len(nearby_stations)}개")
    for station in nearby_stations[:3]:
        print(f"  {station.name}: {station.bikes_available}대 이용가능")
    
    nearby_vehicles = updater.get_vehicles_near(gangnam_lat, gangnam_lon, 300)
    print(f"\n강남역 300m 내 킥보드: {len(nearby_vehicles)}개")
    
    # 백그라운드 업데이트 테스트
    # updater.start()
    # time.sleep(5)
    # updater.stop()