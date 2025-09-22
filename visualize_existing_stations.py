#!/usr/bin/env python3
"""기존 가상 정거장 데이터를 시각화"""

import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from generate_pm_virtual_stations import PMVirtualStationGenerator

def visualize_existing(n_kickboards=500):
    """이미 생성된 데이터 시각화"""
    # 데이터 로드
    output_dir = Path('grid_virtual_stations')
    stations_df = pd.read_csv(output_dir / f'virtual_stations_{n_kickboards}.csv')
    kickboards_df = pd.read_csv(output_dir / f'kickboards_{n_kickboards}.csv')
    
    # 생성기 인스턴스 (시각화 메서드 사용)
    generator = PMVirtualStationGenerator()
    
    # 시각화
    grid_size = stations_df['grid_size_m'].iloc[0]
    generator.visualize_stations(stations_df, kickboards_df, n_kickboards, grid_size)

if __name__ == "__main__":
    # 500개 버전 시각화
    visualize_existing(500)
    
    # 300개 버전도 있으면 시각화
    if Path('grid_virtual_stations/virtual_stations_300.csv').exists():
        visualize_existing(300)