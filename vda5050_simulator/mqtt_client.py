"""MQTT 클라이언트 - VDA5050 토픽 구독/발행 관리."""

from __future__ import annotations

import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt

from .models import _to_dict, _timestamp, ConnectionMessage

logger = logging.getLogger(__name__)


class MqttClient:
    def __init__(self, config: dict):
        mqtt_cfg = config["mqtt"]
        robot_cfg = config["robot"]

        self._broker_host = mqtt_cfg["broker_host"]
        self._broker_port = mqtt_cfg["broker_port"]
        self._manufacturer = robot_cfg["manufacturer"]
        self._serial_number = robot_cfg["serial_number"]
        self._interface = robot_cfg["interface_name"]
        self._version = robot_cfg["protocol_version"]

        self._topic_prefix = (
            f"{self._interface}/{self._version}/"
            f"{self._manufacturer}/{self._serial_number}"
        )

        self._header_ids: dict[str, int] = {}
        self._on_order: Callable | None = None
        self._on_instant_actions: Callable | None = None

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"vda5050_sim_{self._serial_number}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # Last Will: CONNECTIONBROKEN
        last_will = ConnectionMessage(
            headerId=0,
            timestamp=_timestamp(),
            version="2.0.0",
            manufacturer=self._manufacturer,
            serialNumber=self._serial_number,
            connectionState="CONNECTIONBROKEN",
        )
        self._client.will_set(
            topic=f"{self._topic_prefix}/connection",
            payload=json.dumps(_to_dict(last_will)),
            qos=1,
            retain=True,
        )

    def set_callbacks(
        self,
        on_order: Callable | None = None,
        on_instant_actions: Callable | None = None,
    ):
        self._on_order = on_order
        self._on_instant_actions = on_instant_actions

    def connect(self):
        logger.info(
            "MQTT 연결 시도: %s:%d", self._broker_host, self._broker_port
        )
        self._client.connect(self._broker_host, self._broker_port)
        self._client.loop_start()

    def disconnect(self):
        self.publish_connection("OFFLINE")
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("MQTT 연결 종료")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0 or (hasattr(rc, 'value') and rc.value == 0):
            logger.info("MQTT 연결 성공")
            # 토픽 구독
            order_topic = f"{self._topic_prefix}/order"
            ia_topic = f"{self._topic_prefix}/instantActions"
            client.subscribe(order_topic, qos=0)
            client.subscribe(ia_topic, qos=0)
            logger.info("구독: %s, %s", order_topic, ia_topic)
            # ONLINE 발행
            self.publish_connection("ONLINE")
        else:
            logger.error("MQTT 연결 실패: rc=%s", rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        if rc != 0 and not (hasattr(rc, 'value') and rc.value == 0):
            logger.warning("MQTT 비정상 연결 해제: rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("메시지 파싱 실패 [%s]: %s", topic, e)
            return

        if topic.endswith("/order"):
            logger.info("Order 수신: orderId=%s", payload.get("orderId", "?"))
            if self._on_order:
                self._on_order(payload)
        elif topic.endswith("/instantActions"):
            logger.info("InstantActions 수신")
            if self._on_instant_actions:
                self._on_instant_actions(payload)
        else:
            logger.debug("알 수 없는 토픽: %s", topic)

    def _next_header_id(self, topic: str) -> int:
        self._header_ids[topic] = self._header_ids.get(topic, 0) + 1
        return self._header_ids[topic]

    def publish_connection(self, state: str):
        topic = f"{self._topic_prefix}/connection"
        msg = ConnectionMessage(
            headerId=self._next_header_id("connection"),
            timestamp=_timestamp(),
            version="2.0.0",
            manufacturer=self._manufacturer,
            serialNumber=self._serial_number,
            connectionState=state,
        )
        self._client.publish(
            topic, json.dumps(_to_dict(msg)), qos=1, retain=True
        )
        logger.info("Connection 발행: %s", state)

    def publish_state(self, state_dict: dict):
        topic = f"{self._topic_prefix}/state"
        state_dict["headerId"] = self._next_header_id("state")
        state_dict["timestamp"] = _timestamp()
        result = self._client.publish(topic, json.dumps(state_dict, ensure_ascii=False))
        logger.debug("State 발행 → %s (rc=%s)", topic, result.rc)

    def publish_visualization(self, vis_dict: dict):
        topic = f"{self._topic_prefix}/visualization"
        vis_dict["headerId"] = self._next_header_id("visualization")
        vis_dict["timestamp"] = _timestamp()
        self._client.publish(topic, json.dumps(vis_dict, ensure_ascii=False))
