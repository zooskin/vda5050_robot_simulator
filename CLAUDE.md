# VDA5050 Robot Simulator

VDA5050 v2.0 프로토콜 기반 멀티 AGV 시뮬레이터. MQTT를 통해 Master Control의 명령을 수신하고, 여러 대의 로봇 이동을 시간 기반으로 동시 시뮬레이션한다.

## 실행

```bash
# 의존성 설치
pip install paho-mqtt pyyaml

# MQTT 브로커 실행 (별도 터미널)
mosquitto

# 시뮬레이터 실행 (config.yaml의 robots 목록에 정의된 모든 로봇 동시 실행)
python run.py
```

## 설정 (config.yaml)

- `mqtt` - MQTT 브로커 접속 정보
- `robot_defaults` - 모든 로봇에 공통 적용되는 기본값 (manufacturer, max_speed 등)
- `robots` - 로봇 목록. 각 로봇은 `serial_number`와 `initial_position` 필수. `robot_defaults` 값을 개별 override 가능
- `publishing` - State/Visualization 발행 주기

## 구조

- `run.py` - 진입점. 멀티 로봇 config 조합 및 asyncio.gather로 동시 실행
- `config.yaml` - MQTT/로봇 설정
- `vda5050_simulator/models.py` - VDA5050 데이터 모델
- `vda5050_simulator/mqtt_client.py` - MQTT 통신
- `vda5050_simulator/robot.py` - 로봇 상태/이동 시뮬레이션
- `vda5050_simulator/order_manager.py` - 주문 관리
- `vda5050_simulator/action_handler.py` - 액션 처리
- `vda5050_simulator/state_publisher.py` - State/Visualization 발행

## 테스트 (mosquitto_pub 사용)

```bash
# 각 로봇의 State 수신
mosquitto_sub -t "uagv/v2/RobotCompany/AGV_001/state"
mosquitto_sub -t "uagv/v2/RobotCompany/AGV_002/state"
mosquitto_sub -t "uagv/v2/RobotCompany/AGV_003/state"

# 특정 로봇에 Order 전송
mosquitto_pub -t "uagv/v2/RobotCompany/AGV_002/order" -m '{ ... }'
```

## Workflow
- 작업이 완료되면 자동으로 git commit을 수행할 것 (사용자가 별도로 요청하지 않아도)
