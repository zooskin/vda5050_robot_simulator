# VDA5050 Robot Simulator

VDA5050 v2.0 프로토콜 기반 AGV 시뮬레이터. MQTT를 통해 Master Control의 명령을 수신하고, 로봇 이동을 시간 기반으로 시뮬레이션한다.

## 실행

```bash
# 의존성 설치
pip install paho-mqtt pyyaml

# MQTT 브로커 실행 (별도 터미널)
mosquitto

# 시뮬레이터 실행
python run.py
```

## 구조

- `run.py` - 진입점
- `config.yaml` - MQTT/로봇 설정
- `vda5050_simulator/models.py` - VDA5050 데이터 모델
- `vda5050_simulator/mqtt_client.py` - MQTT 통신
- `vda5050_simulator/robot.py` - 로봇 상태/이동 시뮬레이션
- `vda5050_simulator/order_manager.py` - 주문 관리
- `vda5050_simulator/action_handler.py` - 액션 처리
- `vda5050_simulator/state_publisher.py` - State/Visualization 발행

## 테스트 (mosquitto_pub 사용)

```bash
# Order 전송
mosquitto_pub -t "uagv/v2/RobotCompany/AGV-001/order" -m '{ ... }'

# State 수신
mosquitto_sub -t "uagv/v2/RobotCompany/AGV-001/state"
```
