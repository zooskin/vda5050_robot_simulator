# VDA5050 Robot Simulator

VDA5050 v2.0 프로토콜 기반 멀티 AGV 시뮬레이터.
MQTT를 통해 Master Control의 명령을 수신하고, 여러 대의 로봇 이동을 시간 기반으로 동시에 시뮬레이션한다.

## 주요 기능

- **VDA5050 v2.0 준수** - Order, InstantActions, State, Visualization, Connection 메시지 처리
- **멀티 로봇 동시 실행** - `config.yaml`에서 로봇 목록을 정의하여 여러 대를 하나의 프로세스에서 실행
- **시간 기반 이동 시뮬레이션** - 노드 간 직선 이동, 속도/방향/배터리 상태 실시간 갱신
- **Order 생명주기 관리** - 신규 주문, 주문 업데이트(Stitching), 주문 취소 지원
- **Action 처리** - pick, drop, startCharging, stopCharging, startPause, stopPause, cancelOrder, initPosition 등
- **MQTT Last Will** - 비정상 연결 해제 시 `CONNECTIONBROKEN` 자동 발행

## 요구 사항

- Python 3.10+
- MQTT 브로커 (예: [Mosquitto](https://mosquitto.org/))

## 설치 및 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# MQTT 브로커 실행 (별도 터미널)
mosquitto

# 시뮬레이터 실행
python run.py
```

## 설정

`config.yaml`에서 MQTT 접속 정보, 로봇 기본값, 개별 로봇, 발행 주기를 설정한다.

```yaml
mqtt:
  broker_host: "localhost"
  broker_port: 1883

robot_defaults:
  manufacturer: "RobotCompany"
  interface_name: "uagv"
  protocol_version: "v2"
  max_speed: 0.5          # m/s
  rotation_speed: 0.7     # rad/s
  battery:
    initial_charge: 100.0
    discharge_rate: 0.01  # %/s (주행 중)

robots:
  - serial_number: "AGV_001"
    initial_position: { x: 8, y: -10.8, theta: 0.0, map_id: "L1" }
  - serial_number: "AGV_002"
    initial_position: { x: 10, y: -10.8, theta: 0.0, map_id: "L1" }
  - serial_number: "AGV_003"
    initial_position: { x: 12, y: -10.8, theta: 0.0, map_id: "L1" }
    max_speed: 0.8  # 개별 override 가능

publishing:
  state_interval: 0.5       # State 발행 주기 (초)
  visualization_interval: 1.0
  simulation_tick: 0.05     # 시뮬레이션 틱 (50ms)
```

| 섹션 | 설명 |
|------|------|
| `mqtt` | MQTT 브로커 접속 정보 |
| `robot_defaults` | 모든 로봇에 공통 적용되는 기본값 |
| `robots` | 로봇 목록. `serial_number`와 `initial_position` 필수. `robot_defaults` 값을 개별 override 가능 |
| `publishing` | State/Visualization 발행 주기 및 시뮬레이션 틱 |

## MQTT 토픽 구조

VDA5050 v2.0 표준 토픽 형식을 따른다:

```
{interface}/{version}/{manufacturer}/{serialNumber}/{topic}
```

| 방향 | 토픽 | 설명 |
|------|------|------|
| Master -> AGV | `uagv/v2/RobotCompany/AGV_001/order` | 주행 명령 |
| Master -> AGV | `uagv/v2/RobotCompany/AGV_001/instantActions` | 즉시 실행 액션 |
| AGV -> Master | `uagv/v2/RobotCompany/AGV_001/state` | 로봇 상태 |
| AGV -> Master | `uagv/v2/RobotCompany/AGV_001/visualization` | 위치 시각화 데이터 |
| AGV -> Master | `uagv/v2/RobotCompany/AGV_001/connection` | 연결 상태 (retained) |

## 테스트

```bash
# 특정 로봇의 State 수신
mosquitto_sub -t "uagv/v2/RobotCompany/AGV_001/state"

# 모든 로봇의 State 수신
mosquitto_sub -t "uagv/v2/RobotCompany/+/state"

# Order 전송 예시
mosquitto_pub -t "uagv/v2/RobotCompany/AGV_001/order" -m '{
  "headerId": 1,
  "timestamp": "2024-01-01T00:00:00.000Z",
  "version": "2.0.0",
  "manufacturer": "RobotCompany",
  "serialNumber": "AGV_001",
  "orderId": "order-001",
  "orderUpdateId": 0,
  "nodes": [
    {
      "nodeId": "N1",
      "sequenceId": 0,
      "released": true,
      "nodePosition": { "x": 8, "y": -10.8, "mapId": "L1" },
      "actions": []
    },
    {
      "nodeId": "N2",
      "sequenceId": 2,
      "released": true,
      "nodePosition": { "x": 15, "y": -10.8, "mapId": "L1" },
      "actions": []
    }
  ],
  "edges": [
    {
      "edgeId": "E1",
      "sequenceId": 1,
      "released": true,
      "startNodeId": "N1",
      "endNodeId": "N2",
      "actions": []
    }
  ]
}'

# InstantAction으로 일시정지
mosquitto_pub -t "uagv/v2/RobotCompany/AGV_001/instantActions" -m '{
  "headerId": 1,
  "timestamp": "2024-01-01T00:00:00.000Z",
  "version": "2.0.0",
  "manufacturer": "RobotCompany",
  "serialNumber": "AGV_001",
  "actions": [
    {
      "actionType": "startPause",
      "actionId": "pause-001",
      "blockingType": "NONE"
    }
  ]
}'
```

## 지원 액션

| 액션 | 설명 | 소요 시간 |
|------|------|-----------|
| `pick` | 화물 적재 | 3.0초 |
| `drop` | 화물 하역 | 3.0초 |
| `startCharging` | 충전 시작 | 1.0초 |
| `stopCharging` | 충전 중지 | 1.0초 |
| `initPosition` | 위치 초기화 | 0.5초 |
| `finePositioning` | 정밀 위치 조정 | 2.0초 |
| `startPause` | 일시정지 | 즉시 |
| `stopPause` | 일시정지 해제 | 즉시 |
| `cancelOrder` | 주문 취소 | 즉시 |

## 프로젝트 구조

```
vda5050_robot_simulator/
├── run.py                          # 진입점, 멀티 로봇 실행 관리
├── config.yaml                     # MQTT/로봇 설정
├── requirements.txt                # Python 의존성
└── vda5050_simulator/
    ├── models.py                   # VDA5050 v2.0 데이터 모델 (dataclass)
    ├── mqtt_client.py              # MQTT 연결/구독/발행
    ├── robot.py                    # 로봇 상태 관리 및 이동 시뮬레이션
    ├── order_manager.py            # Order 수신/검증/업데이트
    ├── action_handler.py           # Action 생명주기 관리
    └── state_publisher.py          # State/Visualization 주기적 발행
```

## 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                     run.py                          │
│   main() ─── asyncio.gather(sim1, sim2, sim3, ...) │
└────────┬──────────────┬──────────────┬──────────────┘
         │              │              │
    ┌────▼────┐    ┌────▼────┐    ┌────▼────┐
    │Simulator│    │Simulator│    │Simulator│
    │ AGV_001 │    │ AGV_002 │    │ AGV_003 │
    └────┬────┘    └────┬────┘    └────┬────┘
         │              │              │
    각 Simulator 인스턴스는 독립적으로 구성:
    ┌─────────────────────────────┐
    │  Robot (상태/이동 시뮬레이션)  │
    │  MqttClient (MQTT 통신)      │
    │  OrderManager (주문 관리)     │
    │  ActionHandler (액션 처리)    │
    │  StatePublisher (상태 발행)   │
    └─────────────────────────────┘
```

각 `Simulator`는 독립된 MQTT 클라이언트를 가지며, 하나의 asyncio 이벤트 루프에서 동시에 실행된다. 종료 시그널(SIGINT/SIGTERM)은 공유 `stop_event`를 통해 모든 Simulator에 전파된다.
