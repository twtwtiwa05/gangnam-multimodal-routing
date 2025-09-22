#!/usr/bin/env python3
"""
강남구 PM(Personal Mobility) 위치 추출기
실제 스윙 데이터에서 강남구 내 PM 시작/종료 위치 추출
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

# 강남구 경계
GANGNAM_BOUNDS = {
    'min_lat': 37.460, 'max_lat': 37.550,
    'min_lon': 127.000, 'max_lon': 127.140
}

def is_in_gangnam(lat, lon):
    """좌표가 강남구 내에 있는지 확인"""
    return (GANGNAM_BOUNDS['min_lat'] <= lat <= GANGNAM_BOUNDS['max_lat'] and
            GANGNAM_BOUNDS['min_lon'] <= lon <= GANGNAM_BOUNDS['max_lon'])

def extract_gangnam_pm_locations():
    """강남구 내 PM 위치 추출"""
    
    print("스윙 PM 데이터 로드 중...")
    
    # 데이터 파일 경로
    data_path = Path("PM_DATA/2023_0510_Swing_routes.csv")
    
    if not data_path.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {data_path}")
        return
    
    # CSV 파일 읽기
    try:
        df = pd.read_csv(data_path)
        print(f"✅ 총 {len(df):,}개의 주행 기록 로드")
    except Exception as e:
        print(f"❌ 파일 읽기 오류: {e}")
        return
    
    # 좌표 데이터 확인
    print("\n좌표 데이터 확인...")
    print(f"시작 좌표: {df['start_x'].notna().sum():,}개")
    print(f"종료 좌표: {df['end_x'].notna().sum():,}개")
    
    # 강남구 내 시작 위치 필터링 (x가 위도, y가 경도)
    df['start_in_gangnam'] = df.apply(
        lambda row: is_in_gangnam(row['start_x'], row['start_y']) 
        if pd.notna(row['start_x']) and pd.notna(row['start_y']) else False, 
        axis=1
    )
    
    # 강남구 내 종료 위치 필터링 (x가 위도, y가 경도)
    df['end_in_gangnam'] = df.apply(
        lambda row: is_in_gangnam(row['end_x'], row['end_y'])
        if pd.notna(row['end_x']) and pd.notna(row['end_y']) else False,
        axis=1
    )
    
    # 강남구 관련 주행만 필터링
    gangnam_df = df[df['start_in_gangnam'] | df['end_in_gangnam']].copy()
    
    print(f"\n강남구 관련 주행: {len(gangnam_df):,}개")
    print(f"- 강남구에서 시작: {gangnam_df['start_in_gangnam'].sum():,}개")
    print(f"- 강남구에서 종료: {gangnam_df['end_in_gangnam'].sum():,}개")
    print(f"- 강남구 내부 이동: {(gangnam_df['start_in_gangnam'] & gangnam_df['end_in_gangnam']).sum():,}개")
    
    # 시간대별 분포
    if len(gangnam_df) > 0:
        gangnam_df['hour'] = pd.to_datetime(gangnam_df['start_time']).dt.hour
        hourly_dist = gangnam_df['hour'].value_counts().sort_index()
        
        print("\n시간대별 이용 분포:")
        for hour, count in hourly_dist.items():
            bar = '█' * int(count / hourly_dist.max() * 20)
            print(f"{hour:02d}시: {bar} {count:,}개")
    else:
        hourly_dist = pd.Series(dtype=int)
    
    # 주요 위치 클러스터링 (시작점 기준)
    print("\n주요 PM 위치 분석 (시작점 기준)...")
    
    # 강남구 내 시작점만 추출 (x가 위도, y가 경도)
    start_points = gangnam_df[gangnam_df['start_in_gangnam']][['start_x', 'start_y']].dropna()
    
    # 격자 단위로 집계 (약 100m x 100m)
    grid_size = 0.001  # 약 100m
    start_points['grid_lat'] = (start_points['start_x'] / grid_size).round() * grid_size
    start_points['grid_lon'] = (start_points['start_y'] / grid_size).round() * grid_size
    
    # 격자별 카운트
    grid_counts = start_points.groupby(['grid_lat', 'grid_lon']).size().reset_index(name='count')
    grid_counts = grid_counts.sort_values('count', ascending=False)
    
    print(f"\n상위 20개 PM 밀집 지역:")
    for idx, row in grid_counts.head(20).iterrows():
        print(f"{idx+1}. 위도 {row['grid_lat']:.4f}, 경도 {row['grid_lon']:.4f} - {row['count']:,}개 주행")
    
    # 결과 저장
    output_dir = Path("gangnam_pm_data")
    output_dir.mkdir(exist_ok=True)
    
    # 1. 강남구 PM 주행 데이터
    gangnam_df.to_csv(output_dir / "gangnam_swing_routes_20230510.csv", index=False)
    print(f"\n✅ 강남구 PM 주행 데이터 저장: {output_dir}/gangnam_swing_routes_20230510.csv")
    
    # 2. PM 밀집 지역 데이터
    grid_counts.to_csv(output_dir / "gangnam_pm_hotspots.csv", index=False)
    print(f"✅ PM 밀집 지역 데이터 저장: {output_dir}/gangnam_pm_hotspots.csv")
    
    # 3. 통계 요약
    stats = {
        'date': '2023-05-10',
        'total_routes': len(df),
        'gangnam_routes': len(gangnam_df),
        'start_in_gangnam': int(gangnam_df['start_in_gangnam'].sum()),
        'end_in_gangnam': int(gangnam_df['end_in_gangnam'].sum()),
        'within_gangnam': int((gangnam_df['start_in_gangnam'] & gangnam_df['end_in_gangnam']).sum()),
        'peak_hour': int(hourly_dist.idxmax()) if len(hourly_dist) > 0 else -1,
        'peak_hour_count': int(hourly_dist.max()) if len(hourly_dist) > 0 else 0,
        'unique_start_grids': len(grid_counts),
        'bounds': GANGNAM_BOUNDS
    }
    
    with open(output_dir / "gangnam_pm_stats.json", 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"✅ 통계 요약 저장: {output_dir}/gangnam_pm_stats.json")
    
    # 4. 모빌리티 위치 데이터 (PART1_2.py와 호환)
    print("\n모빌리티 위치 데이터 생성 중...")
    
    # 상위 100개 밀집 지역을 킥보드 위치로 사용
    mobility_locations = []
    
    for idx, row in grid_counts.head(100).iterrows():
        # 밀집도에 따라 킥보드 수 결정 (최대 10대)
        n_vehicles = min(10, max(1, int(row['count'] / 10)))
        
        for i in range(n_vehicles):
            # 격자 내에서 약간의 랜덤 오프셋 추가
            offset_x = np.random.uniform(-grid_size/2, grid_size/2)
            offset_y = np.random.uniform(-grid_size/2, grid_size/2)
            
            mobility_locations.append({
                'type': 'kickboard',
                'lat': row['grid_lat'] + offset_y,
                'lon': row['grid_lon'] + offset_x,
                'battery': np.random.uniform(20, 100),  # 20-100% 배터리
                'provider': 'swing'
            })
    
    # 모빌리티 위치 저장
    mobility_df = pd.DataFrame(mobility_locations)
    mobility_df.to_csv(output_dir / "gangnam_kickboard_locations.csv", index=False)
    print(f"✅ 킥보드 위치 데이터 저장: {output_dir}/gangnam_kickboard_locations.csv ({len(mobility_df)}개)")
    
    print("\n완료! 강남구 PM 데이터 추출 완료")
    return gangnam_df, grid_counts

if __name__ == "__main__":
    extract_gangnam_pm_locations()