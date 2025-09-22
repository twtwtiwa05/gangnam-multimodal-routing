#!/usr/bin/env python3
"""
스윙 PM 주행 데이터 기반 가상 정거장 생성기
1. 원본 주행 데이터 분석 
2. 격자 기반 수요 분석
3. 사용자 입력에 따른 킥보드 배치
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from collections import defaultdict
from typing import List, Tuple, Dict
import logging
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import cm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PMVirtualStationGenerator:
    """PM 가상 정거장 생성기"""
    
    def __init__(self, data_dir: str = 'gangnam_pm_data'):
        self.data_dir = Path(data_dir)
        self.bounds = {
            'min_lat': 37.460,
            'max_lat': 37.550, 
            'min_lon': 127.000,
            'max_lon': 127.140
        }
        
    def analyze_swing_routes(self) -> pd.DataFrame:
        """스윙 주행 데이터 분석"""
        logger.info("스윙 주행 데이터 분석 시작...")
        
        # 주행 데이터 로드
        routes_file = self.data_dir / 'gangnam_swing_routes_20230510.csv'
        if not routes_file.exists():
            raise FileNotFoundError(f"주행 데이터 없음: {routes_file}")
            
        # CSV 읽기
        routes_df = pd.read_csv(routes_file)
        logger.info(f"총 {len(routes_df):,}개 주행 기록 로드")
        
        # 강남구 내 주행만 필터링
        # 스윙 데이터는 start_x(위도), start_y(경도) 형식 (일반과 반대!)
        gangnam_routes = routes_df[
            (routes_df['start_x'] >= self.bounds['min_lat']) &
            (routes_df['start_x'] <= self.bounds['max_lat']) &
            (routes_df['start_y'] >= self.bounds['min_lon']) &
            (routes_df['start_y'] <= self.bounds['max_lon'])
        ]
        
        logger.info(f"강남구 내 주행: {len(gangnam_routes):,}개")
        return gangnam_routes
    
    def create_demand_grid(self, routes_df: pd.DataFrame, 
                          grid_size_m: int = 100) -> Dict:
        """격자별 수요 분석"""
        logger.info(f"{grid_size_m}m 격자로 수요 분석...")
        
        # 격자 크기 계산 (도 단위)
        lat_step = grid_size_m / 111000  # 1도 ≈ 111km
        lon_step = grid_size_m / (111000 * np.cos(np.radians(37.5)))
        
        # 격자별 이용 횟수 집계
        grid_demand = defaultdict(int)
        
        # 출발지 집계
        for _, route in routes_df.iterrows():
            grid_lat = round(route['start_x'] / lat_step) * lat_step  # x가 위도
            grid_lon = round(route['start_y'] / lon_step) * lon_step  # y가 경도
            grid_demand[(grid_lat, grid_lon)] += 1
        
        # 도착지도 집계 (가중치 0.5)
        for _, route in routes_df.iterrows():
            grid_lat = round(route['end_x'] / lat_step) * lat_step  # x가 위도 
            grid_lon = round(route['end_y'] / lon_step) * lon_step  # y가 경도
            grid_demand[(grid_lat, grid_lon)] += 0.5
        
        # 정렬된 리스트로 변환
        demand_list = []
        for (lat, lon), count in grid_demand.items():
            demand_list.append({
                'grid_lat': lat,
                'grid_lon': lon,
                'demand': int(count),
                'grid_size_m': grid_size_m
            })
        
        # 수요 높은 순으로 정렬
        demand_list.sort(key=lambda x: x['demand'], reverse=True)
        
        logger.info(f"총 {len(demand_list)}개 격자에서 이용 확인")
        logger.info(f"최대 수요: {demand_list[0]['demand']}회")
        
        return demand_list
    
    def generate_virtual_stations(self, demand_list: List[Dict], 
                                 n_stations: int) -> pd.DataFrame:
        """수요 기반 가상 정거장 생성"""
        logger.info(f"{n_stations}개 가상 정거장 생성...")
        
        # 상위 n개 격자 선택
        top_grids = demand_list[:n_stations]
        
        # 가상 정거장 데이터프레임 생성
        stations = []
        for i, grid in enumerate(top_grids):
            station = {
                'station_id': f'VS_{i+1:04d}',
                'station_name': f'가상정거장_{i+1}',
                'center_lat': grid['grid_lat'],
                'center_lon': grid['grid_lon'],
                'n_kickboards': 0,  # 나중에 배분
                'grid_size_m': grid['grid_size_m'],
                'demand': grid['demand']
            }
            stations.append(station)
        
        stations_df = pd.DataFrame(stations)
        return stations_df
    
    def allocate_kickboards(self, stations_df: pd.DataFrame, 
                           total_kickboards: int) -> pd.DataFrame:
        """수요 비례 킥보드 배분"""
        logger.info(f"총 {total_kickboards}개 킥보드를 {len(stations_df)}개 정거장에 배분...")
        
        # 총 수요
        total_demand = stations_df['demand'].sum()
        
        # 수요 비례 배분
        stations_df['n_kickboards'] = (
            stations_df['demand'] / total_demand * total_kickboards
        ).round().astype(int)
        
        # 최소 1개는 보장
        stations_df.loc[stations_df['n_kickboards'] == 0, 'n_kickboards'] = 1
        
        # 총합 맞추기
        diff = total_kickboards - stations_df['n_kickboards'].sum()
        if diff > 0:
            # 부족하면 상위 정거장에 추가
            for i in range(diff):
                stations_df.loc[i % len(stations_df), 'n_kickboards'] += 1
        elif diff < 0:
            # 초과하면 하위 정거장에서 감소
            for i in range(-diff):
                idx = -(i % len(stations_df)) - 1
                if stations_df.iloc[idx]['n_kickboards'] > 1:
                    stations_df.loc[stations_df.index[idx], 'n_kickboards'] -= 1
        
        logger.info(f"배분 완료: 평균 {stations_df['n_kickboards'].mean():.1f}개/정거장")
        return stations_df
    
    def generate_kickboard_locations(self, stations_df: pd.DataFrame) -> pd.DataFrame:
        """각 정거장 내 킥보드 위치 생성"""
        logger.info("개별 킥보드 위치 생성...")
        
        kickboards = []
        kickboard_id = 1
        
        for _, station in stations_df.iterrows():
            n_kicks = station['n_kickboards']
            grid_size = station['grid_size_m']
            
            # 격자 내 랜덤 위치 생성
            for i in range(n_kicks):
                # 격자 내 랜덤 오프셋 (-0.5 ~ 0.5 격자 크기)
                offset_lat = (np.random.random() - 0.5) * grid_size / 111000
                offset_lon = (np.random.random() - 0.5) * grid_size / (111000 * np.cos(np.radians(37.5)))
                
                kickboard = {
                    'kickboard_id': f'KB_{kickboard_id:05d}',
                    'station_id': station['station_id'],
                    'lat': station['center_lat'] + offset_lat,
                    'lon': station['center_lon'] + offset_lon,
                    'battery': np.random.randint(30, 100),  # 30-100% 랜덤
                    'provider': 'swing'
                }
                kickboards.append(kickboard)
                kickboard_id += 1
        
        kickboards_df = pd.DataFrame(kickboards)
        logger.info(f"총 {len(kickboards_df)}개 킥보드 위치 생성 완료")
        return kickboards_df
    
    def save_results(self, stations_df: pd.DataFrame, 
                    kickboards_df: pd.DataFrame,
                    n_kickboards: int):
        """결과 저장"""
        output_dir = Path('grid_virtual_stations')
        output_dir.mkdir(exist_ok=True)
        
        # 가상 정거장 저장
        stations_file = output_dir / f'virtual_stations_{n_kickboards}.csv'
        stations_df.to_csv(stations_file, index=False)
        logger.info(f"가상 정거장 저장: {stations_file}")
        
        # 킥보드 위치 저장
        kickboards_file = output_dir / f'kickboards_{n_kickboards}.csv'
        kickboards_df.to_csv(kickboards_file, index=False)
        logger.info(f"킥보드 위치 저장: {kickboards_file}")
        
        # 요약 통계 저장
        stats = {
            'n_stations': int(len(stations_df)),
            'n_kickboards': int(n_kickboards),
            'avg_kickboards_per_station': float(stations_df['n_kickboards'].mean()),
            'max_demand': int(stations_df['demand'].max()),
            'total_demand': int(stations_df['demand'].sum()),
            'grid_size_m': int(stations_df['grid_size_m'].iloc[0])
        }
        
        stats_file = output_dir / f'stats_{n_kickboards}.json'
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"통계 저장: {stats_file}")
    
    def visualize_stations(self, stations_df: pd.DataFrame, 
                          kickboards_df: pd.DataFrame,
                          n_kickboards: int, grid_size_m: int):
        """가상 정거장 위치 시각화"""
        logger.info("가상 정거장 시각화 생성 중...")
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
        
        # 1. 왼쪽: 가상 정거장 위치와 수요
        scatter = ax1.scatter(stations_df['center_lon'], 
                            stations_df['center_lat'],
                            s=stations_df['demand'] * 10,  # 수요에 비례한 크기
                            c=stations_df['n_kickboards'],  # 킥보드 수에 따른 색상
                            cmap='YlOrRd',
                            alpha=0.7,
                            edgecolors='black',
                            linewidth=1)
        
        # 상위 10개 정거장 라벨
        top_stations = stations_df.nlargest(10, 'demand')
        for _, station in top_stations.iterrows():
            ax1.annotate(station['station_id'][-3:], 
                       (station['center_lon'], station['center_lat']),
                       fontsize=8, ha='center', va='center')
        
        ax1.set_xlabel('경도 (Longitude)')
        ax1.set_ylabel('위도 (Latitude)')
        ax1.set_title(f'가상 정거장 위치 ({len(stations_df)}개)')
        ax1.grid(True, alpha=0.3)
        
        # 컬러바
        cbar = plt.colorbar(scatter, ax=ax1)
        cbar.set_label('킥보드 배치 수')
        
        # 강남구 경계 표시
        ax1.axhline(y=self.bounds['min_lat'], color='r', linestyle='--', alpha=0.5)
        ax1.axhline(y=self.bounds['max_lat'], color='r', linestyle='--', alpha=0.5)
        ax1.axvline(x=self.bounds['min_lon'], color='r', linestyle='--', alpha=0.5)
        ax1.axvline(x=self.bounds['max_lon'], color='r', linestyle='--', alpha=0.5)
        
        # 2. 오른쪽: 히트맵 스타일 격자 시각화
        # 격자별 수요 재계산
        lat_bins = np.arange(self.bounds['min_lat'], self.bounds['max_lat'], grid_size_m/111000)
        lon_bins = np.arange(self.bounds['min_lon'], self.bounds['max_lon'], grid_size_m/(111000*np.cos(np.radians(37.5))))
        
        # 2D 히스토그램 생성
        demand_grid = np.zeros((len(lat_bins)-1, len(lon_bins)-1))
        
        for _, station in stations_df.iterrows():
            lat_idx = np.digitize(station['center_lat'], lat_bins) - 1
            lon_idx = np.digitize(station['center_lon'], lon_bins) - 1
            
            if 0 <= lat_idx < len(lat_bins)-1 and 0 <= lon_idx < len(lon_bins)-1:
                demand_grid[lat_idx, lon_idx] = station['demand']
        
        # 히트맵 플롯
        im = ax2.imshow(demand_grid, 
                       extent=[self.bounds['min_lon'], self.bounds['max_lon'],
                              self.bounds['min_lat'], self.bounds['max_lat']],
                       origin='lower',
                       cmap='hot_r',
                       aspect='auto',
                       interpolation='nearest')
        
        # 킥보드 위치 점들 (작게)
        ax2.scatter(kickboards_df['lon'], kickboards_df['lat'],
                   s=1, c='blue', alpha=0.3, marker='.')
        
        ax2.set_xlabel('경도 (Longitude)')
        ax2.set_ylabel('위도 (Latitude)')
        ax2.set_title(f'수요 히트맵 및 킥보드 분포 ({n_kickboards}개)')
        ax2.grid(True, alpha=0.3)
        
        # 컬러바
        cbar2 = plt.colorbar(im, ax=ax2)
        cbar2.set_label('격자별 수요')
        
        # 전체 제목
        fig.suptitle(f'스윙 PM 가상 정거장 분석 - {n_kickboards}개 킥보드 배치 ({grid_size_m}m 격자)',
                    fontsize=16, fontweight='bold')
        
        # 통계 정보 추가
        stats_text = (f"총 정거장: {len(stations_df)}개\n"
                     f"총 킥보드: {n_kickboards}개\n"
                     f"평균 배치: {stations_df['n_kickboards'].mean():.1f}개/정거장\n"
                     f"최대 수요: {stations_df['demand'].max()}회")
        
        fig.text(0.02, 0.02, stats_text, 
                transform=fig.transFigure,
                fontsize=10,
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        plt.tight_layout()
        
        # 파일 저장
        output_dir = Path('grid_virtual_stations')
        image_file = output_dir / f'virtual_stations_{n_kickboards}.png'
        plt.savefig(image_file, dpi=300, bbox_inches='tight')
        logger.info(f"시각화 이미지 저장: {image_file}")
        
        # 화면에 표시
        plt.show()


def main():
    """메인 실행 함수"""
    print("🛴 스윙 PM 가상 정거장 생성기")
    print("=" * 50)
    
    generator = PMVirtualStationGenerator()
    
    # 1. 주행 데이터 분석
    try:
        routes_df = generator.analyze_swing_routes()
    except FileNotFoundError as e:
        print(f"❌ 오류: {e}")
        print("gangnam_pm_data/gangnam_swing_routes_20230510.csv 파일이 필요합니다.")
        return
    
    # 2. 격자 수요 분석
    print("\n격자 크기 선택:")
    print("1. 50m (세밀함)")
    print("2. 100m (기본)")
    print("3. 200m (넓음)")
    
    grid_choice = input("선택 (1-3) [2]: ") or "2"
    grid_sizes = {"1": 50, "2": 100, "3": 200}
    grid_size = grid_sizes.get(grid_choice, 100)
    
    demand_list = generator.create_demand_grid(routes_df, grid_size)
    
    # 3. 킥보드 개수 입력
    print(f"\n총 {len(demand_list)}개 수요 격자 발견")
    print("배치할 킥보드 개수를 입력하세요.")
    print("권장: 300개 (적음), 500개 (보통), 1000개 (많음)")
    
    while True:
        try:
            n_kickboards = int(input("킥보드 개수: "))
            if n_kickboards < 10:
                print("최소 10개 이상 입력하세요.")
                continue
            if n_kickboards > 5000:
                print("최대 5000개까지 가능합니다.")
                continue
            break
        except ValueError:
            print("숫자를 입력하세요.")
    
    # 4. 가상 정거장 생성
    # 정거장 수는 킥보드 수의 10-20% 정도
    n_stations = min(len(demand_list), max(10, n_kickboards // 10))
    print(f"\n{n_stations}개 가상 정거장 생성 중...")
    
    stations_df = generator.generate_virtual_stations(demand_list, n_stations)
    
    # 5. 킥보드 배분
    stations_df = generator.allocate_kickboards(stations_df, n_kickboards)
    
    # 6. 개별 킥보드 위치 생성
    kickboards_df = generator.generate_kickboard_locations(stations_df)
    
    # 7. 결과 저장
    generator.save_results(stations_df, kickboards_df, n_kickboards)
    
    # 8. 시각화
    generator.visualize_stations(stations_df, kickboards_df, n_kickboards, grid_size)
    
    # 9. 결과 출력
    print("\n✅ 생성 완료!")
    print(f"- 가상 정거장: {n_stations}개")
    print(f"- 킥보드: {n_kickboards}개") 
    print(f"- 평균 배치: {stations_df['n_kickboards'].mean():.1f}개/정거장")
    print(f"- 최대 수요 정거장: {stations_df.iloc[0]['station_name']} ({stations_df.iloc[0]['demand']}회)")
    
    print("\n생성된 파일:")
    print(f"- grid_virtual_stations/virtual_stations_{n_kickboards}.csv")
    print(f"- grid_virtual_stations/kickboards_{n_kickboards}.csv")
    print(f"- grid_virtual_stations/stats_{n_kickboards}.json")
    print(f"- grid_virtual_stations/virtual_stations_{n_kickboards}.png")


if __name__ == "__main__":
    main()