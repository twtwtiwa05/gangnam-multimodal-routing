# Gangnam Multimodal Transportation Routing System

[한국어](README.md) | English

A high-performance, user-adaptive multimodal transportation routing system for Seoul's Gangnam district that seamlessly integrates public transit (bus, subway) with shared mobility options (bike-sharing, e-scooters) using advanced routing algorithms and real-world data.

## Table of Contents

- [Project Overview](#project-overview)
- [System Architecture](#system-architecture)
- [Key Features](#key-features)
- [Algorithm Evolution](#algorithm-evolution)
- [Installation & Setup](#installation--setup)
- [Usage Examples](#usage-examples)
- [Performance Metrics](#performance-metrics)
- [Data Sources](#data-sources)
- [Future Work](#future-work)

## Project Overview

### Problem Statement
Urban mobility in Seoul's Gangnam district requires seamless integration of multiple transportation modes. Traditional routing systems treat each mode separately, leading to suboptimal routes and poor user experience. This project addresses these challenges by implementing a unified multimodal routing algorithm.

### Solution
We developed a comprehensive routing system that treats all transportation modes (buses, subways, public bikes, e-scooters) as part of a single network, enabling true multimodal journey planning with user preference adaptation.

### Key Innovations
1. **Unified Network Model**: All transportation modes integrated into a single graph structure
2. **Zone-Based Routing**: 30×30 grid system for efficient mobility option discovery
3. **User-Adaptive Algorithm**: Dynamic route selection based on user preferences
4. **Real-World Data Integration**: Actual GTFS transit data and mobility usage patterns

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Raw Data Sources                Processing               Integrated System  │
│  ================                ==========               =================  │
│                                                                             │
│  GTFS Transit Data ──┐          GTFSLOADER2.py                             │
│  (202303)            ├────►     - BOM encoding fix       ┌─────────────┐   │
│                      │          - Data validation    ────►│             │   │
│  Subway Transfer ────┘          - 0.33% error rate       │  PART1_2.py │   │
│  Metadata                                                 │             │   │
│                                                          │  Builds:    │   │
│  Seoul Bike         ─────►     process_ttareungee.py    │  - Stops    │   │
│  Stations (693)               - Station processing   ────►│  - Routes   │   │
│                               - Availability model        │  - Trips    │   │
│                                                          │  - Transfers│   │
│  Swing PM Data      ─────►     GangnamMobilityGen.py    │             │   │
│  (9,591 rides)                - Demand analysis     ────►│             │   │
│                               - Grid clustering          └──────┬──────┘   │
│                               - Virtual stations                │          │
│                                                                │          │
│  OSM Road Network   ─────►     Cached as pkl/graphml           │          │
│  (Gangnam bounds)            - Road distance calc              ▼          │
│                                                         raptor_data.pkl    │
│                                                                │          │
└────────────────────────────────────────────────────────────────┼──────────┘
                                                                 │
┌────────────────────────────────────────────────────────────────▼──────────┐
│                           ROUTING ALGORITHMS                               │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Evolution of Algorithms:                                                   │
│                                                                             │
│  1. PART2_NEW (Baseline)          2. PART2_OTP              3. PART2_HYBRID│
│     ==================               ===========                ============│
│     Traditional RAPTOR               Virtual Stop              Zone-Based   │
│     + Separate mobility              Approach                  Multimodal   │
│     - O(n²) complexity              + All modes               + Adaptive   │
│     - 10-20s query time             as stops                  routing     │
│     - Memory intensive              + O(n) scan               + OSM paths  │
│                                     - Memory issues            + User prefs │
│                                     - 480x faster              + Efficient  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Features

### 1. Multimodal Integration
- **Public Transit**: 12,064 stops, 944 routes from GTFS data
- **Bike Sharing (Ttareungee)**: 693 stations with real-time availability modeling
- **E-Scooters**: 500 units distributed across 50 virtual stations based on usage patterns
- **Walking**: OSM-based actual road distances for accurate pedestrian routing

### 2. User-Adaptive Routing
The system adapts to individual user preferences through configurable weights:

```python
preference = RoutePreference(
    time_weight=0.4,        # Journey time importance
    transfer_weight=0.3,    # Minimize transfers
    walk_weight=0.2,        # Minimize walking distance
    cost_weight=0.1,        # Cost efficiency
    mobility_preference={
        'bike': 0.9,        # Strong preference for bikes
        'kickboard': 0.4,   # Lower preference for e-scooters
        'ebike': 0.7        # Moderate preference for e-bikes
    }
)
```

### 3. Zone-Based Strategy
Dynamic routing strategies based on origin-destination distance:

| Zone Distance | Strategy | Mobility Weight | Transit Weight |
|--------------|----------|-----------------|----------------|
| 0 (same zone) | mobility_only | 100% | 0% |
| 1 (adjacent) | mobility_first | 80% | 20% |
| 2 zones | mobility_preferred | 70% | 30% |
| 3 zones | balanced | 50% | 50% |
| 4 zones | transit_preferred | 30% | 70% |
| 5+ zones | transit_first | 20% | 80% |

## Algorithm Evolution

### Phase 1: PART2_NEW (Traditional Multimodal RAPTOR)
- **Approach**: Standard RAPTOR with separate mobility search
- **Issues**: 
  - O(n²) complexity for mobility options
  - 10-20 second query times
  - Memory intensive (>1GB)
  - Complex codebase with multiple branches

### Phase 2: PART2_OTP (Virtual Stop Innovation)
- **Breakthrough**: Treat all mobility options as virtual transit stops
- **Benefits**:
  - 480x performance improvement (0.02-0.04s queries)
  - Unified graph structure
  - Simplified algorithm
- **Limitations**:
  - Memory explosion with large datasets
  - Fixed virtual routes lack flexibility

### Phase 3: PART2_HYBRID (Zone-Based Adaptive)
- **Innovation**: 30×30 zone grid with lazy evaluation
- **Advantages**:
  - Adaptive routing based on distance
  - User preference integration
  - OSM-based actual road distances
  - Memory efficient
  - Real-time strategy adjustment

## Installation & Setup

### Prerequisites
```bash
Python 3.8+
pip install pandas numpy networkx scipy
pip install osmnx folium shapely  # Optional but recommended
```

### Data Pipeline Execution
```bash
# 1. Clean and prepare GTFS data
python GTFSLOADER2.py

# 2. Generate shared mobility virtual stations
python GangnamMobilityGenerator.py
# Enter number of vehicles when prompted (e.g., 500)

# 3. Build RAPTOR data structures
python PART1_2.py

# 4. Run routing system
python PART2_HYBRID.py  # Recommended
# or
python PART2_NEW.py     # Traditional approach
# or
python PART2_OTP.py     # Virtual stop approach
```

## Usage Examples

### Basic Route Query
```python
from PART2_HYBRID import HybridZoneRAPTOR, RoutePreference, ZoneConfig

# Initialize with custom configuration
config = ZoneConfig()
config.mobility_only_threshold = 2  # Use only mobility within 2 zones
raptor = HybridZoneRAPTOR(config=config)

# Set user preferences
preference = RoutePreference(
    time_weight=0.4,
    cost_weight=0.1,
    transfer_weight=0.3,
    walk_weight=0.2,
    max_walk_distance=800
)

# Find routes
routes = raptor.find_routes(
    origin=(37.4979, 127.0276),      # Gangnam Station
    destination=(37.5088, 127.0631),  # Samsung Station
    departure_time="08:30",
    preference=preference
)
```

### Example Output
```
Zone Distance: 7, Strategy: transit_first
Mobility weight: 0.20, Transit weight: 0.80

Found 3 routes:

Route 1 (hybrid, strategy: transit_first)
  Total time: 10.2 minutes
  Total cost: 1,370 won
  Transfers: 0
  Walking: 150m
  Segments:
    - Walk: Origin → Gangnam Station (2 min)
    - Subway Line 2: Gangnam → Samsung (6 min)
    - Walk: Samsung Station → Destination (2.2 min)

Route 2 (mobility_only, strategy: direct)
  Total time: 15.3 minutes
  Total cost: 3,200 won
  Transfers: 0
  Walking: 0m
  Segments:
    - Kickboard: Origin → Destination (15.3 min, 4.2km)

Route 3 (hybrid, strategy: transit_first)
  Total time: 18.5 minutes
  Total cost: 2,740 won
  Transfers: 1
  Walking: 320m
  Segments:
    - Walk: Origin → Bus Stop (3 min)
    - Bus 146: Gangnam → Cheongdam (8 min)
    - Walk: Transfer (2 min)
    - Bus 3414: Cheongdam → Samsung (4 min)
    - Walk: Bus Stop → Destination (1.5 min)
```

## Performance Metrics

### Query Performance
| Algorithm | Average Query Time | Memory Usage | Complexity |
|-----------|-------------------|--------------|------------|
| PART2_NEW | 10-20 seconds | ~1GB | O(n²m) |
| PART2_OTP | 0.02-0.04 seconds | ~2GB | O(nm) |
| PART2_HYBRID | 0.5-2 seconds | ~500MB | O(n log n) |

### Data Scale
- Transit stops: 12,064 (9,404 in Gangnam area)
- Transit routes: 944
- Scheduled trips: 44,634
- Transfer connections: 45,406
- Virtual PM stations: 50 (500 vehicles)
- Bike stations: 693
- OSM road network: ~50,000 nodes

### Accuracy Improvements
- GTFS data error rate: Reduced from 83% to 0.33%
- Walking distances: Actual road paths vs 1.3x straight line
- Transfer counting: Fixed to count only actual route changes

## Data Sources

1. **GTFS Transit Data** (March 2023)
   - Source: Korea Transport Database (KTDB)
   - Coverage: Seoul metropolitan area
   - Format: Standard GTFS with Korean encoding

2. **Seoul Bike Stations**
   - Source: Seoul Open Data Portal
   - Stations: 693 in Gangnam district
   - Updated: Real-time availability model

3. **Swing PM Usage Data** (May 10, 2023)
   - Rides: 9,591 in Gangnam area
   - Used for: Demand-based station placement
   - Privacy: Anonymized trip data

4. **OpenStreetMap Road Network**
   - Coverage: Gangnam district bounds
   - Usage: Actual walking/riding distances
   - Format: NetworkX graph (pkl/graphml)

## Technical Challenges & Solutions

### 1. Subway Line 2 Circular Route
- **Challenge**: Circular routes with inner/outer directions
- **Solution**: Enhanced pattern detection in PART1_2.py
- **Status**: Requires further refinement for branch handling

### 2. Korean Text Encoding
- **Challenge**: Mixed encodings (BOM, UTF-8, CP949)
- **Solution**: Automatic encoding detection in GTFSLOADER2.py

### 3. Memory Optimization
- **Challenge**: OTP approach created 20M+ timetable entries
- **Solution**: Zone-based lazy evaluation in HYBRID

### 4. Real-time Adaptability
- **Challenge**: Static pre-computation vs dynamic preferences
- **Solution**: Runtime strategy selection based on zone distance

## Future Work

1. **Real-time Integration**
   - Live transit updates
   - Dynamic mobility availability
   - Traffic conditions

2. **Enhanced User Modeling**
   - Learning from user choices
   - Personalized route recommendations
   - Context-aware suggestions

3. **Subway Line 2 Refinement**
   - Complete circular route handling
   - Branch line integration
   - Express service modeling

4. **Scalability**
   - Extend to entire Seoul metropolitan area
   - Multi-city support
   - Cloud deployment

## Contributing

This project is open for contributions. Key areas:
- Algorithm optimization
- Data quality improvements
- UI/UX development
- Real-time data integration

## License

MIT License - See LICENSE file for details

## Acknowledgments

- Korea Transport Database (KTDB) for GTFS data
- Seoul Metropolitan Government for bike station data
- Swing for anonymized PM usage data
- OpenStreetMap contributors

---

## Author Information

**Name**: [Enter your name]  
**University**: [Enter university name]  
**Department**: [Enter department name]  
**Email**: [Enter email address]  

**Advisor**: [Enter advisor name] (Optional)

---

**Project Period**: November 2024 - December 2024  
**Research Focus**: Multimodal Transportation, Urban Mobility, Algorithm Optimization  
**Keywords**: RAPTOR, Multimodal Routing, Public Transit, Shared Mobility, Seoul