# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **user-adaptive multimodal transportation routing system** for Seoul's Gangnam district that implements multiple variations of the RAPTOR (Round-based Public transit Routing Algorithm) to find optimal routes combining:
- Public transit (buses, subway) via GTFS data
- Seoul bike-sharing (따릉이/Ttareungee) stations with 693 locations
- Shared mobility (e-scooters/kickboards) distributed across 50 virtual stations
- Walking connections with OSM-based actual road distances

The system has evolved through three major algorithmic approaches:
1. **PART2_NEW**: Traditional multimodal RAPTOR with separate mobility search
2. **PART2_OTP**: Virtual stop approach treating all modes as transit stops  
3. **PART2_HYBRID**: Zone-based adaptive routing with user preferences

## Core Commands

### Data Pipeline Execution Order
```bash
# 1. Clean GTFS data (handles BOM encoding issues)
python GTFSLOADER2.py

# 2. Generate shared mobility data (interactive - asks for vehicle counts)
# Note: Uses existing gangnam_road_network.pkl/graphml if available
python GangnamMobilityGenerator.py

# 3. Build RAPTOR data structures
# Note: Uses existing gangnam_road_network.pkl/graphml if available
python PART1_2.py

# 4. Run routing algorithms (in order of recommendation)
python PART2_HYBRID.py     # Zone-based adaptive (RECOMMENDED)
python PART2_OTP.py        # Virtual stop approach
python PART2_NEW.py        # Traditional multimodal
```

### Testing & Debugging Commands
```bash
# Check subway line 2 circular route
python debug_subway_line2.py

# Verify bus routes
python check_bus_routes.py

# Find specific stations
python find_gangnam_seolleung.py

# Visualize virtual stations
python visualize_existing_stations.py
```

### Required Files
**Pre-existing OSM Network** (should be already available):
- `gangnam_road_network.pkl` - Pickled NetworkX graph (preferred)
- `gangnam_road_network.graphml` - GraphML format graph (alternative)

The system will automatically use these existing files instead of downloading OSM data again.

### Dependencies
```bash
pip install pandas numpy networkx
pip install osmnx scipy folium shapely  # Optional but recommended
```

## Architecture Evolution

### Phase 1: PART2_NEW (Traditional Approach)
- Standard RAPTOR with separate mobility layer
- O(n²) complexity for mobility search
- 10-20 second query times
- Memory intensive but flexible

### Phase 2: PART2_OTP (Virtual Stop Innovation)  
- All mobility options as virtual transit stops
- 480x performance improvement
- Memory explosion issues with large datasets
- Fixed virtual routes lack adaptability

### Phase 3: PART2_HYBRID (Zone-Based Solution)
- 30×30 zone grid with lazy evaluation
- Adaptive routing based on O-D distance
- User preference integration
- OSM-based actual road distances
- Optimal balance of performance and flexibility

## Key Data Structures

**Stop Types** (`PART1_2.py:Stop.stop_type`):
- `0`: Bus stops
- `1`: Subway stations  
- `2`: 따릉이 bike stations
- `3`: Shared kickboard/e-bike locations

**Core Classes**:
- `Stop`: Transit stops/stations with lat/lon coordinates
- `Route`: Transit routes with GTFS route information
- `Trip`: Individual transit trips with timetables
- `Transfer`: Walking connections between stops
- `Journey`: Complete route with segments, time, cost, transfers
- `RoutePreference`: User preferences for route optimization
- `Zone`: Grid cells for zone-based routing
- `RoutingStrategy`: Distance-based routing strategies

### Geographic Bounds
**Gangnam District**: 37.460°-37.550°N, 127.000°-127.140°E

## PART2_HYBRID Key Features

### Zone-Based Strategy Configuration
```python
distance_strategies = {
    0: ("mobility_only", 1.0, 0.0),      # Same zone
    1: ("mobility_first", 0.8, 0.2),     # Adjacent zone
    2: ("mobility_preferred", 0.7, 0.3), # 2 zone difference
    3: ("balanced", 0.5, 0.5),           # 3 zone difference
    4: ("transit_preferred", 0.3, 0.7),  # 4 zone difference
    5: ("transit_first", 0.2, 0.8),      # 5 zone difference
    "default": ("transit_only", 0.1, 0.9) # 6+ zones
}
```

### User Preference System
```python
preference = RoutePreference(
    time_weight=0.4,        # Journey time importance
    transfer_weight=0.3,    # Minimize transfers
    walk_weight=0.2,        # Minimize walking
    cost_weight=0.1,        # Cost efficiency
    mobility_preference={
        'bike': 0.9,        # Strong preference for bikes
        'kickboard': 0.4,   # Lower preference for e-scooters
        'ebike': 0.7        # Moderate preference for e-bikes
    }
)
```

### OSM Integration
- Actual road distances for all walking segments
- Road-based distances for bike/kickboard routes
- Cached distance calculations for performance
- Fallback to haversine distance × 1.3 when OSM unavailable

## Data Scales
- **Stops**: 12,064 total (9,404 in Gangnam area)
- **Routes**: 944 transit routes
- **Trips**: 44,634 scheduled trips
- **Transfers**: 45,406 walking connections
- **Virtual Stations**: 50 PM stations (500 vehicles)
- **Bike Stations**: 693 따릉이 locations
- **Zone Grid**: 30×30 = 900 zones
- **Data size**: ~50MB RAPTOR pickle file

## Performance Metrics

### Query Performance
| Algorithm | Average Query Time | Memory Usage | Complexity |
|-----------|-------------------|--------------|------------|
| PART2_NEW | 10-20 seconds | ~1GB | O(n²m) |
| PART2_OTP | 0.02-0.04 seconds | ~2GB | O(nm) |
| PART2_HYBRID | 0.5-2 seconds | ~500MB | O(n log n) |

### Optimization Techniques
- **Lazy Evaluation**: Compute connections only when needed
- **Zone Caching**: Reuse zone connection calculations
- **Distance Caching**: Store OSM distance calculations
- **Limited Propagation**: Top 5 stops per mobility round
- **Early Termination**: Stop when no improvements

## Recent Updates (2025-09-23)

### New Features
1. **PART2_HYBRID Implementation**: Complete zone-based multimodal system
2. **Virtual Station Generation**: Demand-based PM station placement from Swing data
3. **OSM Distance Integration**: Actual road distances for all mobility modes
4. **Route Information Display**: Shows specific bus/subway line numbers
5. **User Preference System**: Configurable weights for time, cost, transfers, walking
6. **Mobility Mode Preferences**: Separate preferences for bike vs kickboard

### Fixed Issues
1. **Memory Optimization**: From 20M+ timetable entries to efficient lazy loading
2. **Query Performance**: Reduced from minutes to seconds with zone approach
3. **Duplicate Routes**: Enhanced filtering and merging of similar routes
4. **Transit Line Display**: Now shows "Bus 146" or "Subway Line 3" instead of generic "transit"
5. **Coordinate Accuracy**: Fixed station coordinates for major locations

### Known Issues
1. **Subway Line 2**: Circular route with branches needs refinement
2. **Express Services**: Not yet modeled in the system
3. **Real-time Updates**: Currently uses static schedule data

## Troubleshooting

### Common Issues
1. **"File not found"**: Run data pipeline in order (GTFSLOADER2 → GangnamMobilityGenerator → PART1_2)
2. **Memory errors**: Use PART2_HYBRID instead of PART2_OTP for large datasets
3. **Empty results**: Check if coordinates are within Gangnam bounds
4. **Import errors**: OSMnx/SciPy are optional - code has fallbacks
5. **Slow queries**: Ensure OSM network files are cached locally

### Data Pipeline Issues
- **Encoding errors**: GTFSLOADER2 automatically fixes BOM issues
- **Virtual stations**: Run GangnamMobilityGenerator.py with desired vehicle count
- **OSM network**: System uses cached pkl/graphml files if available

## Example Output
```
Zone Distance: 2, Strategy: mobility_preferred
Mobility weight: 0.70, Transit weight: 0.30

Found 4 routes:

Route 1 (mobility_only, strategy: direct)
  Total time: 2.5 minutes
  Total cost: 2,600 won
  Transfers: 0
  Walking: 0m
  Segments:
    - Kickboard: (37.4979, 127.0276) → (37.5007, 127.0363)

Route 2 (mobility_only, strategy: direct)
  Total time: 5.9 minutes
  Total cost: 1,000 won
  Transfers: 0
  Walking: 277m
  Segments:
    - Walk: (37.4979, 127.0276) → (37.49847, 127.030113)
    - 따릉이: (37.49847, 127.030113) → (37.5007, 127.0363)

Route 3 (hybrid, strategy: mobility_preferred)
  Total time: 6.3 minutes
  Total cost: 1,370 won
  Transfers: 0
  Walking: 25m
  Segments:
    - Walk: (37.4979, 127.0276) → 강남
    - Bus 740: 강남역.역삼세무서 → 한서병원
    - Walk: 역삼 → (37.5007, 127.0363)
```

## Korean Terms & Translations
- `따릉이` (Ttareungee): Seoul's public bike-sharing system
- `내선/외선`: Inner/outer circle lines (subway)
- `정류장`: Stop/station
- `환승`: Transfer
- `킥보드`: Kickboard (e-scooter)
- `대여소`: Rental station
- `가상 정거장`: Virtual station
- `존`: Zone
- `선호도`: Preference