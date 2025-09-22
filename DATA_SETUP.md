# 데이터 설정 가이드

이 프로젝트를 실행하기 위해서는 다음 데이터가 필요합니다:

## 1. GTFS 대중교통 데이터

한국교통안전공단(KTDB)에서 다운로드:
- https://www.ktdb.go.kr/
- 2023년 3월 서울시 GTFS 데이터셋
- `202303_GTFS_DataSet/` 디렉토리에 압축 해제

필요한 파일:
- agency.txt
- stops.txt  
- routes.txt
- trips.txt
- stop_times.txt
- shapes.txt (선택사항)

## 2. 서울시 따릉이 대여소 데이터

서울 열린데이터 광장에서 다운로드:
- https://data.seoul.go.kr/
- "서울시 공공자전거 대여소 정보" 검색
- CSV 형식으로 다운로드

## 3. 스윙 PM 데이터 (선택사항)

교육/연구 목적으로만 사용:
- 2023년 5월 10일 스윙 주행 데이터
- 강남구 지역 필터링 필요
- 개인정보 익명화 확인

## 4. OSM 도로망 데이터

시스템이 자동으로 생성하거나, 기존 파일 사용:
- `gangnam_road_network.pkl`
- `gangnam_road_network.graphml`

## 디렉토리 구조

```
multimodal_project2/
├── 202303_GTFS_DataSet/        # GTFS 원본 데이터
├── cleaned_gtfs_data/          # 정제된 GTFS (자동 생성)
├── grid_virtual_stations/      # 가상 정거장 (자동 생성)
├── gangnam_raptor_data/        # RAPTOR 데이터 (자동 생성)
└── PM_DATA/                    # 스윙 데이터 (선택사항)
```

## 빠른 시작

최소한의 데이터로 시스템 테스트:
1. GTFS 데이터만 준비
2. `python GTFSLOADER2.py` 실행
3. `python PART1_2.py` 실행
4. `python PART2_HYBRID.py` 실행