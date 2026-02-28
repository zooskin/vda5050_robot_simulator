"""로봇 상태 관리 및 이동 시뮬레이션."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional, Callable

from .models import (
    Node,
    Edge,
    Action,
    ActionState,
    AgvPosition,
    Velocity,
    BatteryState,
    SafetyState,
    Load,
    NodeState,
    EdgeState,
    ErrorEntry,
    InfoEntry,
    MapState,
)

logger = logging.getLogger(__name__)


class Robot:
    def __init__(self, config: dict):
        robot_cfg = config["robot"]
        pos_cfg = robot_cfg["initial_position"]
        bat_cfg = robot_cfg["battery"]
        pub_cfg = config["publishing"]

        self._manufacturer = robot_cfg["manufacturer"]
        self._serial_number = robot_cfg["serial_number"]
        self._max_speed = robot_cfg["max_speed"]
        self._rotation_speed = robot_cfg["rotation_speed"]
        self._tick = pub_cfg["simulation_tick"]

        # 위치
        self.position_x: float = pos_cfg["x"]
        self.position_y: float = pos_cfg["y"]
        self.position_theta: float = pos_cfg["theta"]
        self.map_id: str = pos_cfg["map_id"]
        self.position_initialized: bool = True

        # 속도
        self.velocity_vx: float = 0.0
        self.velocity_vy: float = 0.0
        self.velocity_omega: float = 0.0

        # 배터리
        self._battery_charge: float = bat_cfg["initial_charge"]
        self._discharge_rate: float = bat_cfg["discharge_rate"]
        self._charge_rate: float = bat_cfg.get("charge_rate", 0.1)
        self.battery_state_charging: bool = False

        # 주문 상태
        self.order_id: str = ""
        self.order_update_id: int = 0
        self.last_node_id: str = ""
        self.last_node_sequence_id: int = 0
        self.driving: bool = False
        self.new_base_request: bool = False
        self.distance_since_last_node: float = 0.0

        # 운영 상태
        self.operating_mode: str = "AUTOMATIC"
        self.paused: bool = False

        # 상태 배열
        self.node_states: list[NodeState] = []
        self.edge_states: list[EdgeState] = []
        self.action_states: list[ActionState] = []
        self.loads: list[Load] = []
        self.errors: list[ErrorEntry] = []
        self.information: list[InfoEntry] = []
        self.maps: list[MapState] = [
            MapState(mapId=pos_cfg["map_id"], mapVersion="1.0", mapStatus="ENABLED")
        ]
        self.safety_state = SafetyState(eStop="NONE", fieldViolation=False)

        # 내부 주행 상태
        self._nodes: list[Node] = []
        self._edges: list[Edge] = []
        self._current_node_index: int = 0  # 다음 목표 노드 인덱스
        self._current_edge_index: int = 0  # 현재 주행 중인 엣지 인덱스
        self._navigation_active: bool = False
        self._order_cancelled: bool = False
        self._download_map_pending_action_id: str | None = None

        # 상태 변경 콜백
        self._state_change_callback: Callable | None = None
        # action_handler는 나중에 설정
        self._action_handler = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_action_handler(self, handler):
        self._action_handler = handler

    def set_state_change_callback(self, callback: Callable):
        self._state_change_callback = callback

    def notify_state_change(self):
        if self._state_change_callback:
            self._state_change_callback()

    def set_download_map_pending(self, action_id: str):
        """downloadMap 액션 pending 설정."""
        self._download_map_pending_action_id = action_id
        logger.info("downloadMap pending 설정: %s", action_id)

    def is_download_map_ready(self) -> bool:
        """downloadMap 완료 여부 확인."""
        if self._download_map_pending_action_id is None:
            return True
        for astate in self.action_states:
            if astate.actionId == self._download_map_pending_action_id:
                if astate.actionStatus in ("FINISHED", "FAILED"):
                    return True
                return False
        return True

    def apply_order(
        self,
        order_id: str,
        order_update_id: int,
        nodes: list[Node],
        edges: list[Edge],
        node_states: list[NodeState],
        edge_states: list[EdgeState],
    ):
        """검증된 주문을 로봇에 적용하고 주행을 시작한다."""
        self.order_id = order_id
        self.order_update_id = order_update_id
        self._nodes = nodes
        self._edges = edges
        self.node_states = node_states
        self.edge_states = edge_states
        self._order_cancelled = False
        self.new_base_request = False

        # 액션 상태 생성
        self.action_states = []
        for node in nodes:
            for action in node.actions:
                astate = self._action_handler.create_action_state(action, "WAITING")
                self.action_states.append(astate)
        for edge in edges:
            for action in edge.actions:
                astate = self._action_handler.create_action_state(action, "WAITING")
                self.action_states.append(astate)

        # 첫 노드(stitching 노드) 처리
        first_node = nodes[0]
        already_at_first = (self.last_node_id == first_node.nodeId)

        if already_at_first:
            # 이미 stitching 노드에 도착한 상태
            if first_node.nodePosition:
                self.position_x = first_node.nodePosition.x
                self.position_y = first_node.nodePosition.y
                if first_node.nodePosition.theta is not None:
                    self.position_theta = first_node.nodePosition.theta
                self.map_id = first_node.nodePosition.mapId

            self.last_node_id = first_node.nodeId
            self.last_node_sequence_id = first_node.sequenceId
            self.distance_since_last_node = 0.0

            # 첫 노드를 nodeStates에서 제거 (이미 통과)
            self.node_states = [ns for ns in self.node_states if ns.sequenceId != first_node.sequenceId]

            # 네비게이션 시작 설정
            self._current_node_index = 1  # 다음 목표는 두 번째 노드
            self._current_edge_index = 0

            if len(nodes) > 1:
                self._navigation_active = True
                self.driving = True
            else:
                self._navigation_active = False
                self.driving = False

            # 첫 노드 액션 트리거
            asyncio.ensure_future(self._trigger_node_actions(first_node))
        else:
            # 아직 stitching 노드에 도착하지 않은 상태
            # 현재 위치를 유지하고 stitching 노드를 향해 계속 이동
            self._current_node_index = 0
            self._current_edge_index = -1  # stitching 노드 이전에는 이 order의 엣지 없음
            self._navigation_active = True
            self.driving = True

        self.notify_state_change()
        logger.info(
            "주문 적용: %s, 노드 %d개, 엣지 %d개",
            order_id, len(nodes), len(edges),
        )

    def cancel_current_order(self):
        """현재 주문을 취소한다."""
        self._order_cancelled = True
        self._navigation_active = False
        self.paused = False
        self.driving = False
        self.velocity_vx = 0.0
        self.velocity_vy = 0.0
        self.velocity_omega = 0.0
        self.node_states = []
        self.edge_states = []
        self.order_id = ""
        self.order_update_id = 0
        self.new_base_request = False
        logger.info("주문 취소됨, 로봇 정지")

    async def navigation_loop(self):
        """메인 네비게이션 루프. asyncio 태스크로 실행."""
        while True:
            await asyncio.sleep(self._tick)

            if not self._navigation_active or self.paused or self._order_cancelled:
                self.velocity_vx = 0.0
                self.velocity_vy = 0.0
                self.velocity_omega = 0.0
                if self.paused and self._navigation_active:
                    self.driving = False
                continue

            # downloadMap 완료 대기
            if not self.is_download_map_ready():
                self.velocity_vx = 0.0
                self.velocity_vy = 0.0
                self.velocity_omega = 0.0
                self.driving = False
                continue

            if self._current_node_index >= len(self._nodes):
                # 모든 노드 통과 완료
                self._navigation_active = False
                self.driving = False
                self.velocity_vx = 0.0
                self.velocity_vy = 0.0
                self.velocity_omega = 0.0
                self.notify_state_change()
                logger.info("모든 노드 주행 완료")
                continue

            target_node = self._nodes[self._current_node_index]

            # released 확인 - Horizon 노드는 주행하지 않음
            if not target_node.released:
                self.new_base_request = True
                self.driving = False
                self.velocity_vx = 0.0
                self.velocity_vy = 0.0
                self.velocity_omega = 0.0
                continue
            else:
                self.new_base_request = False
                self.driving = True

            if target_node.nodePosition is None:
                # 위치 없는 노드는 즉시 통과
                await self._arrive_at_node(target_node)
                continue

            target_x = target_node.nodePosition.x
            target_y = target_node.nodePosition.y
            deviation = target_node.nodePosition.allowedDeviationXY or 0.5

            # 현재 위치에서 목표까지 거리
            dx = target_x - self.position_x
            dy = target_y - self.position_y
            distance = math.sqrt(dx * dx + dy * dy)

            # 현재 엣지의 maxSpeed 또는 기본 속도
            speed = self._max_speed
            if 0 <= self._current_edge_index < len(self._edges):
                edge = self._edges[self._current_edge_index]
                if edge.maxSpeed is not None:
                    speed = edge.maxSpeed

            # 노드 도달 확인
            if distance <= deviation:
                await self._arrive_at_node(target_node)
                continue

            # 이동
            move_dist = speed * self._tick
            if move_dist >= distance:
                move_dist = distance

            if distance > 0:
                ratio = move_dist / distance
                self.position_x += dx * ratio
                self.position_y += dy * ratio

            # 방향 계산
            self.position_theta = math.atan2(dy, dx)

            # 속도 업데이트
            self.velocity_vx = (dx / distance) * speed if distance > 0 else 0.0
            self.velocity_vy = (dy / distance) * speed if distance > 0 else 0.0
            self.velocity_omega = 0.0

            # 이동 거리 누적
            self.distance_since_last_node += move_dist

            # 배터리 소모
            self._battery_charge -= self._discharge_rate * self._tick
            if self._battery_charge < 0:
                self._battery_charge = 0

    async def _arrive_at_node(self, node: Node):
        """노드 도착 처리."""
        logger.info("노드 도착: %s (seq=%d)", node.nodeId, node.sequenceId)

        # 정확한 위치로 맞춤
        if node.nodePosition:
            self.position_x = node.nodePosition.x
            self.position_y = node.nodePosition.y
            if node.nodePosition.theta is not None:
                self.position_theta = node.nodePosition.theta

        # nodeStates에서 제거
        self.node_states = [
            ns for ns in self.node_states if ns.sequenceId != node.sequenceId
        ]

        # 이전 엣지 완료 (edgeStates에서 제거)
        if 0 <= self._current_edge_index < len(self._edges):
            completed_edge = self._edges[self._current_edge_index]
            self.edge_states = [
                es for es in self.edge_states if es.sequenceId != completed_edge.sequenceId
            ]
            # 엣지 액션이 아직 WAITING이면 FINISHED로 처리
            for action in completed_edge.actions:
                for astate in self.action_states:
                    if astate.actionId == action.actionId and astate.actionStatus == "WAITING":
                        astate.actionStatus = "FINISHED"

        # lastNodeId 업데이트
        self.last_node_id = node.nodeId
        self.last_node_sequence_id = node.sequenceId
        self.distance_since_last_node = 0.0

        # 노드 액션 트리거
        await self._trigger_node_actions(node)

        # 다음 노드/엣지로 이동
        self._current_node_index += 1
        self._current_edge_index += 1

        # 다음 엣지 액션 트리거
        if self._current_edge_index < len(self._edges):
            next_edge = self._edges[self._current_edge_index]
            if next_edge.released:
                await self._trigger_edge_actions(next_edge)

        # 모든 Base 노드 완료 확인
        remaining_base = [ns for ns in self.node_states if ns.released]
        if not remaining_base and self._current_node_index >= len(self._nodes):
            self.driving = False
            self._navigation_active = False
            logger.info("모든 주행 완료")

        self.notify_state_change()

    async def _trigger_node_actions(self, node: Node):
        """노드의 액션을 트리거."""
        for action in node.actions:
            for astate in self.action_states:
                if astate.actionId == action.actionId and astate.actionStatus == "WAITING":
                    if action.blockingType == "HARD":
                        # HARD 액션: 완료될 때까지 대기
                        await self._action_handler.trigger_action(action, astate)
                        # HARD 블로킹이면 완료 대기
                        while astate.actionStatus in ("INITIALIZING", "RUNNING"):
                            await asyncio.sleep(0.1)
                    else:
                        # NONE/SOFT: 비동기 실행
                        await self._action_handler.trigger_action(action, astate)

    async def _trigger_edge_actions(self, edge: Edge):
        """엣지의 액션을 트리거."""
        for action in edge.actions:
            for astate in self.action_states:
                if astate.actionId == action.actionId and astate.actionStatus == "WAITING":
                    await self._action_handler.trigger_action(action, astate)

    async def charging_loop(self):
        """충전 시뮬레이션 루프."""
        while True:
            await asyncio.sleep(1.0)
            if self.battery_state_charging and self._battery_charge < 100.0:
                old_charge = self._battery_charge
                self._battery_charge = min(
                    100.0, self._battery_charge + self._charge_rate
                )
                old_tens = int(old_charge) // 10
                new_tens = int(self._battery_charge) // 10
                if new_tens > old_tens:
                    logger.info(
                        "배터리 충전 중: %.1f%% (rate=%.1f%%/s)",
                        self._battery_charge, self._charge_rate,
                    )

    def get_agv_position(self) -> AgvPosition:
        return AgvPosition(
            x=round(self.position_x, 4),
            y=round(self.position_y, 4),
            theta=round(self.position_theta, 4),
            mapId=self.map_id,
            positionInitialized=self.position_initialized,
            localizationScore=1.0,
        )

    def get_velocity(self) -> Velocity:
        return Velocity(
            vx=round(self.velocity_vx, 4),
            vy=round(self.velocity_vy, 4),
            omega=round(self.velocity_omega, 4),
        )

    def get_battery_state(self) -> BatteryState:
        return BatteryState(
            batteryCharge=round(self._battery_charge, 1),
            charging=self.battery_state_charging,
            batteryVoltage=48.0,
            reach=int(self._battery_charge * 50),
        )
