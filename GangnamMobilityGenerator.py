"""
강남구 공유 킥보드/전기자전거 생성기 v2.0
- 스테이션 없음, 도로 위 자유 주차
- 개별 기기들이 도로에 흩어져 있음
- 실제처럼 여러 대가 붙어있을 수 있음
"""

import numpy as np
import pandas as pd
from shapely.geometry import Point
import osmnx as ox
import random
from typing import List, Dict
from pathlib import Path
import json
import folium
from datetime import datetime
import pickle
import os

class GangnamSharedMobilityGenerator:
    """강남구 공유 모빌리티 생성기 - 자유 주차 버전"""
    
    def __init__(self, num_kickboards: int = 500, num_ebikes: int = 300):
        """
        초기화
        
        Args:
            num_kickboards: 생성할 개별 킥보드 수
            num_ebikes: 생성할 개별 전기자전거 수
        """
        self.num_kickboards = num_kickboards
        self.num_ebikes = num_ebikes
        
        # 강남구 경계
        self.gangnam_bounds = {
            'north': 37.540,
            'south': 37.460,
            'east': 127.100,
            'west': 127.010
        }
        
        # OSM 도로 네트워크
        self.road_network = None
        self.valid_nodes = []  # 주차 가능한 노드
        
        # 생성된 개별 기기들
        self.kickboards = []  # 개별 킥보드
        self.ebikes = []      # 개별 전기자전거
        
        print("🛴 강남구 공유 모빌리티 생성기 v2.0")
        print(f"   목표: 킥보드 {num_kickboards}대, 전기자전거 {num_ebikes}대")
    
    def download_road_network(self) -> bool:
        """기존 OSM 도로망 로드 또는 다운로드"""
        print("\n📍 OSM 도로망 로딩...")
        
        # 1. 기존 파일 시도
        for path in ["gangnam_road_network.pkl", "gangnam_road_network.graphml"]:
            if os.path.exists(path):
                try:
                    if path.endswith('.pkl'):
                        with open(path, 'rb') as f:
                            self.road_network = pickle.load(f)
                    else:
                        import networkx as nx
                        self.road_network = nx.read_graphml(path)
                    
                    print(f"   ✅ 기존 OSM 로드: {self.road_network.number_of_nodes():,}개 노드")
                    print(f"   ✅ 기존 OSM 로드: {self.road_network.number_of_edges():,}개 엣지")
                    
                    self._filter_valid_nodes()
                    return True
                    
                except Exception as e:
                    print(f"   ⚠️ {path} 로드 실패: {e}")
                    continue
        
        # 2. 기존 파일 없으면 새로 다운로드
        print("   ⚠️ 기존 OSM 파일 없음, 새로 다운로드...")
        try:
            self.road_network = ox.graph_from_bbox(
                north=self.gangnam_bounds['north'],
                south=self.gangnam_bounds['south'],
                east=self.gangnam_bounds['east'],
                west=self.gangnam_bounds['west'],
                network_type='all'
            )
            
            print(f"   ✅ 새 OSM 다운로드: {self.road_network.number_of_nodes():,}개 노드")
            print(f"   ✅ 새 OSM 다운로드: {self.road_network.number_of_edges():,}개 엣지")
            
            # 새로 다운로드한 네트워크 저장
            try:
                with open("gangnam_road_network.pkl", 'wb') as f:
                    pickle.dump(self.road_network, f)
                print(f"   💾 새 OSM 저장: gangnam_road_network.pkl")
            except Exception as e:
                print(f"   ⚠️ OSM 저장 실패: {e}")
            
            self._filter_valid_nodes()
            return True
            
        except Exception as e:
            print(f"   ❌ OSM 다운로드 실패: {e}")
            print("   🔄 합성 네트워크 생성...")
            return self._generate_synthetic_network()
    
    def _filter_valid_nodes(self):
        """주차 가능한 노드 필터링 (고속도로, 터널 제외)"""
        print("   🔍 주차 가능 위치 필터링...")
        
        valid_nodes = []
        
        for node, data in self.road_network.nodes(data=True):
            edges = self.road_network.edges(node, data=True)
            
            is_valid = True
            for u, v, edge_data in edges:
                highway_type = edge_data.get('highway', '')
                
                # 고속도로, 자동차전용도로만 제외
                excluded = ['motorway', 'motorway_link', 'trunk', 'trunk_link']
                
                if isinstance(highway_type, list):
                    highway_type = highway_type[0]
                
                if any(ex in str(highway_type) for ex in excluded):
                    is_valid = False
                    break
            
            if is_valid:
                lat = data.get('y', 0)
                lon = data.get('x', 0)
                
                if (self.gangnam_bounds['south'] <= lat <= self.gangnam_bounds['north'] and
                    self.gangnam_bounds['west'] <= lon <= self.gangnam_bounds['east']):
                    valid_nodes.append({
                        'node_id': node,
                        'lat': lat,
                        'lon': lon
                    })
        
        self.valid_nodes = valid_nodes
        print(f"   ✅ 주차 가능 위치: {len(self.valid_nodes):,}개")
    
    def _generate_synthetic_network(self) -> bool:
        """OSM 실패시 합성 네트워크 생성"""
        print("   🏗️ 합성 도로망 생성...")
        
        # 100m 간격 격자
        lat_steps = 80
        lon_steps = 80
        
        lats = np.linspace(
            self.gangnam_bounds['south'], 
            self.gangnam_bounds['north'], 
            lat_steps
        )
        lons = np.linspace(
            self.gangnam_bounds['west'], 
            self.gangnam_bounds['east'], 
            lon_steps
        )
        
        self.valid_nodes = []
        node_id = 0
        
        for lat in lats:
            for lon in lons:
                # 70% 확률로 노드 생성 (도로가 있는 곳)
                if random.random() < 0.7:
                    # 실제 도로처럼 약간의 불규칙성
                    lat_noise = np.random.normal(0, 0.00005)
                    lon_noise = np.random.normal(0, 0.00005)
                    
                    self.valid_nodes.append({
                        'node_id': node_id,
                        'lat': lat + lat_noise,
                        'lon': lon + lon_noise
                    })
                    node_id += 1
        
        print(f"   ✅ 합성 노드 생성: {len(self.valid_nodes):,}개")
        return True
    
    def generate_vehicles(self):
        """개별 킥보드와 전기자전거 생성"""
        print("\n🛴 공유 모빌리티 배치 중...")
        
        if not self.valid_nodes:
            print("   ❌ 유효한 노드가 없습니다.")
            return
        
        # 1. 킥보드 생성
        self._generate_kickboards()
        
        # 2. 전기자전거 생성
        self._generate_ebikes()
        
        print(f"\n✅ 생성 완료!")
        print(f"   킥보드: {len(self.kickboards)}대")
        print(f"   전기자전거: {len(self.ebikes)}대")
    
    def _generate_kickboards(self):
        """개별 킥보드 생성 및 배치"""
        print("   🛴 킥보드 배치...")
        
        # 클러스터링 효과: 일부 지점에 여러 대가 몰려있음
        cluster_points = random.sample(self.valid_nodes, min(50, len(self.valid_nodes)))
        
        kickboard_id = 0
        
        while kickboard_id < self.num_kickboards:
            # 70% 확률로 클러스터 지점 근처, 30%는 랜덤
            if random.random() < 0.7 and cluster_points:
                # 클러스터 지점 선택
                cluster = random.choice(cluster_points)
                base_lat = cluster['lat']
                base_lon = cluster['lon']
                
                # 클러스터 내 2-5대 생성
                cluster_size = random.randint(2, 5)
                
                for _ in range(min(cluster_size, self.num_kickboards - kickboard_id)):
                    # 클러스터 중심에서 약간 떨어진 위치 (5-20m)
                    offset_lat = np.random.normal(0, 0.00002)
                    offset_lon = np.random.normal(0, 0.00002)
                    
                    kickboard = {
                        'vehicle_id': f'KB_{kickboard_id:05d}',
                        'lat': base_lat + offset_lat,
                        'lon': base_lon + offset_lon,
                        'provider': random.choice(['Beam', 'Lime', 'Kickgoing', 'Swing', 'Xingxing']),
                        'battery_level': random.uniform(0.1, 1.0),  # 10-100%
                        'is_available': random.random() > 0.1,  # 90% 이용 가능
                        'price_per_min': random.choice([100, 150, 200]),
                        'unlock_price': 1000,
                        'max_speed': 25,
                        'last_used': datetime.now().isoformat(),
                        'condition': random.choice(['good', 'good', 'normal', 'needs_repair'])  # 상태
                    }
                    self.kickboards.append(kickboard)
                    kickboard_id += 1
            else:
                # 랜덤 위치
                node = random.choice(self.valid_nodes)
                
                kickboard = {
                    'vehicle_id': f'KB_{kickboard_id:05d}',
                    'lat': node['lat'],
                    'lon': node['lon'],
                    'provider': random.choice(['Beam', 'Lime', 'Kickgoing', 'Swing', 'Xingxing']),
                    'battery_level': random.uniform(0.1, 1.0),
                    'is_available': random.random() > 0.1,
                    'price_per_min': random.choice([100, 150, 200]),
                    'unlock_price': 1000,
                    'max_speed': 25,
                    'last_used': datetime.now().isoformat(),
                    'condition': random.choice(['good', 'good', 'normal', 'needs_repair'])
                }
                self.kickboards.append(kickboard)
                kickboard_id += 1
        
        print(f"      ✅ {len(self.kickboards)}대 킥보드 배치 완료")
    
    def _generate_ebikes(self):
        """개별 전기자전거 생성 및 배치"""
        print("   🚴 전기자전거 배치...")
        
        # 전기자전거도 클러스터링 (주로 지하철역 근처 등)
        cluster_points = random.sample(self.valid_nodes, min(30, len(self.valid_nodes)))
        
        ebike_id = 0
        
        while ebike_id < self.num_ebikes:
            # 60% 확률로 클러스터, 40%는 분산
            if random.random() < 0.6 and cluster_points:
                cluster = random.choice(cluster_points)
                base_lat = cluster['lat']
                base_lon = cluster['lon']
                
                # 클러스터 내 1-3대
                cluster_size = random.randint(1, 3)
                
                for _ in range(min(cluster_size, self.num_ebikes - ebike_id)):
                    offset_lat = np.random.normal(0, 0.00003)
                    offset_lon = np.random.normal(0, 0.00003)
                    
                    ebike = {
                        'vehicle_id': f'EB_{ebike_id:05d}',
                        'lat': base_lat + offset_lat,
                        'lon': base_lon + offset_lon,
                        'provider': random.choice(['Kakao T Bike', 'Elecle', 'GCOO', 'Alpaca']),
                        'battery_level': random.uniform(0.2, 1.0),  # 20-100%
                        'is_available': random.random() > 0.15,  # 85% 이용 가능
                        'price_per_min': random.choice([150, 200, 250]),
                        'unlock_price': 1500,
                        'max_speed': 30,
                        'range_km': random.uniform(10, 50),  # 남은 주행거리
                        'last_used': datetime.now().isoformat(),
                        'condition': random.choice(['good', 'good', 'good', 'normal'])  # 자전거는 상태 좋음
                    }
                    self.ebikes.append(ebike)
                    ebike_id += 1
            else:
                node = random.choice(self.valid_nodes)
                
                ebike = {
                    'vehicle_id': f'EB_{ebike_id:05d}',
                    'lat': node['lat'],
                    'lon': node['lon'],
                    'provider': random.choice(['Kakao T Bike', 'Elecle', 'GCOO', 'Alpaca']),
                    'battery_level': random.uniform(0.2, 1.0),
                    'is_available': random.random() > 0.15,
                    'price_per_min': random.choice([150, 200, 250]),
                    'unlock_price': 1500,
                    'max_speed': 30,
                    'range_km': random.uniform(10, 50),
                    'last_used': datetime.now().isoformat(),
                    'condition': random.choice(['good', 'good', 'good', 'normal'])
                }
                self.ebikes.append(ebike)
                ebike_id += 1
        
        print(f"      ✅ {len(self.ebikes)}대 전기자전거 배치 완료")
    
    def save_vehicles(self, output_dir: str = "shared_mobility"):
        """생성된 개별 기기들 저장"""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        print(f"\n💾 데이터 저장: {output_dir}/")
        
        # 1. 킥보드 CSV
        kb_df = pd.DataFrame(self.kickboards)
        kb_df.to_csv(output_path / 'kickboards.csv', index=False, encoding='utf-8')
        print(f"   ✅ kickboards.csv ({len(self.kickboards)}대)")
        
        # 2. 전기자전거 CSV
        eb_df = pd.DataFrame(self.ebikes)
        eb_df.to_csv(output_path / 'ebikes.csv', index=False, encoding='utf-8')
        print(f"   ✅ ebikes.csv ({len(self.ebikes)}대)")
        
        # 3. 통합 GeoJSON
        self._save_geojson(output_path)
        
        # 4. 메타데이터
        metadata = {
            'created_at': datetime.now().isoformat(),
            'bounds': self.gangnam_bounds,
            'statistics': {
                'total_kickboards': len(self.kickboards),
                'total_ebikes': len(self.ebikes),
                'available_kickboards': sum(1 for k in self.kickboards if k['is_available']),
                'available_ebikes': sum(1 for e in self.ebikes if e['is_available']),
                'providers': {
                    'kickboard': list(set(k['provider'] for k in self.kickboards)),
                    'ebike': list(set(e['provider'] for e in self.ebikes))
                },
                'avg_battery': {
                    'kickboard': np.mean([k['battery_level'] for k in self.kickboards]),
                    'ebike': np.mean([e['battery_level'] for e in self.ebikes])
                }
            }
        }
        
        with open(output_path / 'metadata.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"   ✅ metadata.json")
    
    def _save_geojson(self, output_path: Path):
        """GeoJSON 저장"""
        features = []
        
        # 킥보드
        for kb in self.kickboards:
            if kb['is_available']:  # 이용 가능한 것만 표시
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [kb['lon'], kb['lat']]
                    },
                    'properties': {
                        'type': 'kickboard',
                        'id': kb['vehicle_id'],
                        'provider': kb['provider'],
                        'battery': kb['battery_level'],
                        'price': kb['price_per_min']
                    }
                })
        
        # 전기자전거
        for eb in self.ebikes:
            if eb['is_available']:
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [eb['lon'], eb['lat']]
                    },
                    'properties': {
                        'type': 'ebike',
                        'id': eb['vehicle_id'],
                        'provider': eb['provider'],
                        'battery': eb['battery_level'],
                        'range_km': eb['range_km']
                    }
                })
        
        geojson = {
            'type': 'FeatureCollection',
            'features': features
        }
        
        with open(output_path / 'shared_mobility.geojson', 'w', encoding='utf-8') as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        print(f"   ✅ shared_mobility.geojson")


# 실행 코드
if __name__ == "__main__":
    print("=" * 60)
    print("🛴 강남구 공유 모빌리티 생성기 - 자유 주차 방식")
    print("=" * 60)
    
    # 사용자 입력
    print("\n📊 생성할 개별 기기 수:")
    
    try:
        num_kb = input("   킥보드 대수 (기본: 500): ").strip()
        num_kb = int(num_kb) if num_kb else 500
        
        num_eb = input("   전기자전거 대수 (기본: 300): ").strip()
        num_eb = int(num_eb) if num_eb else 300
        
        # 생성기 초기화
        generator = GangnamSharedMobilityGenerator(
            num_kickboards=num_kb,
            num_ebikes=num_eb
        )
        
        # 도로망 다운로드 또는 합성
        try:
            generator.download_road_network()
        except:
            print("   ⚠️ OSMnx 미설치, 합성 네트워크 사용")
            generator._generate_synthetic_network()
        
        # 개별 기기 생성
        generator.generate_vehicles()
        
        # 저장
        generator.save_vehicles("shared_mobility")
        
        print("\n🎉 완료!")
        print("   📁 shared_mobility/ 폴더에 저장됨")
        print("\n특징:")
        print("   - 개별 킥보드/자전거가 도로 위에 자유롭게 주차")
        print("   - 일부 지점에 2-5대씩 클러스터링")
        print("   - 배터리 레벨과 이용 가능 상태 포함")
        
    except Exception as e:
        print(f"\n❌ 오류: {e}")