"""VDA5050 Robot Simulator 진입점."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

import yaml

from vda5050_simulator.mqtt_client import MqttClient
from vda5050_simulator.robot import Robot
from vda5050_simulator.action_handler import ActionHandler
from vda5050_simulator.order_manager import OrderManager
from vda5050_simulator.state_publisher import StatePublisher
from vda5050_simulator.models import parse_instant_actions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vda5050_sim")


class Simulator:
    def __init__(self, config: dict):
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._robot = Robot(config)
        self._action_handler = ActionHandler(self._robot)
        self._robot.set_action_handler(self._action_handler)
        self._order_manager = OrderManager(self._robot)
        self._mqtt = MqttClient(config)
        self._state_publisher = StatePublisher(self._robot, self._mqtt, config)

        # MQTT 콜백 등록 (paho-mqtt 스레드에서 호출됨)
        self._mqtt.set_callbacks(
            on_order=self._handle_order,
            on_instant_actions=self._handle_instant_actions,
        )

    def _handle_order(self, payload: dict):
        """Order 메시지 수신 콜백. (paho-mqtt 스레드에서 호출)"""
        # asyncio 루프에 스케줄링
        if self._loop:
            self._loop.call_soon_threadsafe(self._process_order, payload)

    def _process_order(self, payload: dict):
        """Order 처리. (asyncio 스레드에서 실행)"""
        success = self._order_manager.process_order(payload)
        if success:
            logger.info("주문 수락 완료")
        else:
            logger.warning("주문 거부됨")
        self._state_publisher.publish_state_now()

    def _handle_instant_actions(self, payload: dict):
        """InstantActions 메시지 수신 콜백. (paho-mqtt 스레드에서 호출)"""
        if self._loop:
            self._loop.call_soon_threadsafe(self._process_instant_actions, payload)

    def _process_instant_actions(self, payload: dict):
        """InstantActions 처리. (asyncio 스레드에서 실행)"""
        try:
            ia = parse_instant_actions(payload)
        except (KeyError, TypeError, ValueError) as e:
            logger.error("InstantActions 파싱 실패: %s", e)
            return

        for action in ia.actions:
            logger.info("InstantAction 수신: %s (id=%s)", action.actionType, action.actionId)
            asyncio.ensure_future(self._action_handler.execute_instant_action(action))

    async def run(self):
        """시뮬레이터 실행."""
        self._loop = asyncio.get_running_loop()
        # Robot과 StatePublisher에도 loop 참조 전달
        self._robot.set_loop(self._loop)
        self._state_publisher.set_loop(self._loop)

        robot_cfg = self._config["robot"]
        logger.info("=" * 60)
        logger.info("VDA5050 Robot Simulator 시작")
        logger.info("  제조사: %s", robot_cfg["manufacturer"])
        logger.info("  시리얼: %s", robot_cfg["serial_number"])
        logger.info("  초기 위치: (%.1f, %.1f)", robot_cfg["initial_position"]["x"],
                     robot_cfg["initial_position"]["y"])
        logger.info("=" * 60)

        # MQTT 연결
        try:
            self._mqtt.connect()
        except Exception as e:
            logger.error("MQTT 브로커 연결 실패: %s", e)
            logger.error("mosquitto가 실행 중인지 확인하세요")
            return

        # 초기 State 즉시 발행
        await asyncio.sleep(0.5)  # MQTT 연결 안정화 대기
        self._state_publisher.publish_state_now()

        # 비동기 태스크 시작
        tasks = [
            asyncio.create_task(self._robot.navigation_loop()),
            asyncio.create_task(self._robot.charging_loop()),
            asyncio.create_task(self._state_publisher.state_publish_loop()),
            asyncio.create_task(self._state_publisher.visualization_publish_loop()),
        ]

        # 종료 시그널 대기
        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("종료 시그널 수신")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        # 정리
        logger.info("시뮬레이터 종료 중...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        self._mqtt.disconnect()
        logger.info("시뮬레이터 종료 완료")


def main():
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml을 찾을 수 없습니다: %s", config_path)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sim = Simulator(config)
    asyncio.run(sim.run())


if __name__ == "__main__":
    main()
