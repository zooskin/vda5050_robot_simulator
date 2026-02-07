"""VDA5050 Robot Simulator 진입점."""

import asyncio
import copy
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


def _build_robot_config(mqtt_cfg: dict, pub_cfg: dict, defaults: dict, robot_entry: dict) -> dict:
    """robot_defaults와 개별 로봇 설정을 merge하여 단일 로봇 config를 생성."""
    robot_cfg = copy.deepcopy(defaults)
    for key, value in robot_entry.items():
        if isinstance(value, dict) and key in robot_cfg and isinstance(robot_cfg[key], dict):
            robot_cfg[key].update(value)
        else:
            robot_cfg[key] = value
    return {
        "mqtt": mqtt_cfg,
        "robot": robot_cfg,
        "publishing": pub_cfg,
    }


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

        self._serial = config["robot"]["serial_number"]

    def _handle_order(self, payload: dict):
        """Order 메시지 수신 콜백. (paho-mqtt 스레드에서 호출)."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._process_order, payload)

    def _process_order(self, payload: dict):
        """Order 처리. (asyncio 스레드에서 실행)."""
        success = self._order_manager.process_order(payload)
        if success:
            logger.info("[%s] 주문 수락 완료", self._serial)
        else:
            logger.warning("[%s] 주문 거부됨", self._serial)
        self._state_publisher.publish_state_now()

    def _handle_instant_actions(self, payload: dict):
        """InstantActions 메시지 수신 콜백. (paho-mqtt 스레드에서 호출)."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._process_instant_actions, payload)

    def _process_instant_actions(self, payload: dict):
        """InstantActions 처리. (asyncio 스레드에서 실행)."""
        try:
            ia = parse_instant_actions(payload)
        except (KeyError, TypeError, ValueError) as e:
            logger.error("[%s] InstantActions 파싱 실패: %s", self._serial, e)
            return

        for action in ia.actions:
            logger.info("[%s] InstantAction 수신: %s (id=%s)", self._serial, action.actionType, action.actionId)
            asyncio.ensure_future(self._action_handler.execute_instant_action(action))

    async def run(self, stop_event: asyncio.Event):
        """시뮬레이터 실행. stop_event가 set되면 종료."""
        self._loop = asyncio.get_running_loop()
        self._robot.set_loop(self._loop)
        self._state_publisher.set_loop(self._loop)

        robot_cfg = self._config["robot"]
        logger.info("=" * 60)
        logger.info("[%s] VDA5050 Robot Simulator 시작", self._serial)
        logger.info("  제조사: %s", robot_cfg["manufacturer"])
        logger.info("  시리얼: %s", robot_cfg["serial_number"])
        logger.info("  초기 위치: (%.1f, %.1f)", robot_cfg["initial_position"]["x"],
                     robot_cfg["initial_position"]["y"])
        logger.info("=" * 60)

        # MQTT 연결
        try:
            self._mqtt.connect()
        except Exception as e:
            logger.error("[%s] MQTT 브로커 연결 실패: %s", self._serial, e)
            logger.error("mosquitto가 실행 중인지 확인하세요")
            return

        # 초기 State 즉시 발행
        await asyncio.sleep(0.5)
        self._state_publisher.publish_state_now()

        # 비동기 태스크 시작
        tasks = [
            asyncio.create_task(self._robot.navigation_loop()),
            asyncio.create_task(self._robot.charging_loop()),
            asyncio.create_task(self._state_publisher.state_publish_loop()),
            asyncio.create_task(self._state_publisher.visualization_publish_loop()),
        ]

        await stop_event.wait()

        # 정리
        logger.info("[%s] 시뮬레이터 종료 중...", self._serial)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        self._mqtt.disconnect()
        logger.info("[%s] 시뮬레이터 종료 완료", self._serial)


def main():
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml을 찾을 수 없습니다: %s", config_path)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    mqtt_cfg = config["mqtt"]
    pub_cfg = config["publishing"]
    defaults = config.get("robot_defaults", {})
    robots = config.get("robots", [])

    if not robots:
        logger.error("robots 목록이 비어 있습니다. config.yaml을 확인하세요.")
        sys.exit(1)

    # 각 로봇별 Simulator 인스턴스 생성
    simulators = []
    for robot_entry in robots:
        robot_config = _build_robot_config(mqtt_cfg, pub_cfg, defaults, robot_entry)
        simulators.append(Simulator(robot_config))

    logger.info("총 %d대의 로봇 시뮬레이터를 시작합니다.", len(simulators))

    async def run_all():
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("종료 시그널 수신")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await asyncio.gather(*(sim.run(stop_event) for sim in simulators))

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
