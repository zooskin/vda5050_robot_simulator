"""Action 생명주기 관리."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .models import Action, ActionState, Load, MapState

if TYPE_CHECKING:
    from .robot import Robot
    from .order_manager import OrderManager

logger = logging.getLogger(__name__)

# 액션 시뮬레이션 소요 시간 (초)
ACTION_DURATIONS = {
    "pick": 5.0,
    "drop": 5.0,
    "startCharging": 1.0,
    "stopCharging": 1.0,
    "initPosition": 0.5,
    "finePositioning": 2.0,
    "startPause": 0.0,  # 즉시 완료
    "stopPause": 0.0,
    "cancelOrder": 0.0,
    "factsheetRequest": 0.0,
    "downloadMap": 3.0,  # 맵 다운로드 시뮬레이션
}


class ActionHandler:
    def __init__(self, robot: Robot, order_manager: OrderManager | None = None, action_results: dict | None = None):
        self._robot = robot
        self._order_manager = order_manager
        self._action_results = action_results or {}
        self._running_tasks: dict[str, asyncio.Task] = {}

    def create_action_state(self, action: Action, initial_status: str = "WAITING") -> ActionState:
        return ActionState(
            actionId=action.actionId,
            actionType=action.actionType,
            actionStatus=initial_status,
            actionDescription=action.actionDescription,
        )

    async def execute_instant_action(self, action: Action):
        """InstantAction으로 수신된 액션을 즉시 실행."""
        action_state = self.create_action_state(action, "INITIALIZING")
        self._robot.action_states.append(action_state)
        self._robot.notify_state_change()

        await self._run_action(action, action_state)

    async def trigger_action(self, action: Action, action_state: ActionState):
        """노드/엣지 도달 시 대기 중이던 액션을 실행."""
        if action_state.actionStatus != "WAITING":
            return
        action_state.actionStatus = "INITIALIZING"
        self._robot.notify_state_change()

        task = asyncio.create_task(self._run_action(action, action_state))
        self._running_tasks[action.actionId] = task

    async def _run_action(self, action: Action, action_state: ActionState):
        action_type = action.actionType
        logger.info("액션 실행: %s (id=%s)", action_type, action.actionId)

        action_state.actionStatus = "RUNNING"
        self._robot.notify_state_change()

        try:
            if action_type == "startPause":
                self._robot.paused = True
                logger.info("일시정지 활성화")

            elif action_type == "stopPause":
                self._robot.paused = False
                logger.info("일시정지 해제")

            elif action_type == "cancelOrder":
                await self._handle_cancel_order(action_state)
                return  # cancelOrder는 자체적으로 FINISHED 처리

            elif action_type == "startCharging":
                self._robot.battery_state_charging = True
                logger.info("충전 시작")

            elif action_type == "stopCharging":
                self._robot.battery_state_charging = False
                logger.info("충전 중지")

            elif action_type == "pick":
                await self._handle_pick(action)

            elif action_type == "drop":
                await self._handle_drop(action)

            elif action_type == "initPosition":
                self._handle_init_position(action)

            elif action_type == "downloadMap":
                self._robot.set_download_map_pending(action.actionId)
                self._handle_download_map(action)

            else:
                # 알 수 없는 액션은 일정 시간 후 완료
                logger.info("일반 액션 실행: %s", action_type)

            duration = ACTION_DURATIONS.get(action_type, 2.0)
            if duration > 0:
                await asyncio.sleep(duration)

            # config의 action_results 설정에 따라 성공/실패 결정
            configured_result = self._action_results.get(action_type, "FINISHED")
            if configured_result == "FAILED":
                action_state.actionStatus = "FAILED"
                action_state.resultDescription = f"{action_type} failed (configured)"
                logger.warning("액션 실패 (설정): %s", action.actionId)
            else:
                action_state.actionStatus = "FINISHED"
                action_state.resultDescription = f"{action_type} completed"
                logger.info("액션 완료: %s", action.actionId)

        except asyncio.CancelledError:
            action_state.actionStatus = "FAILED"
            action_state.resultDescription = "Action cancelled"
            logger.info("액션 취소됨: %s", action.actionId)

        finally:
            self._running_tasks.pop(action.actionId, None)
            self._robot.notify_state_change()

    async def _handle_cancel_order(self, action_state: ActionState):
        """주문 취소 처리."""
        logger.info("주문 취소 처리 시작")

        # 실행 중인 다른 액션을 취소
        for task_id, task in list(self._running_tasks.items()):
            if task_id != action_state.actionId:
                task.cancel()

        # 대기 중인 액션을 FAILED로 변경
        for astate in self._robot.action_states:
            if astate.actionId == action_state.actionId:
                continue
            if astate.actionStatus in ("WAITING", "INITIALIZING", "RUNNING"):
                astate.actionStatus = "FAILED"
                astate.resultDescription = "Cancelled by cancelOrder"

        # 이동 중지
        self._robot.cancel_current_order()

        # OrderManager의 현재 주문도 클리어
        if self._order_manager:
            self._order_manager.clear_order()

        action_state.actionStatus = "FINISHED"
        action_state.resultDescription = "Order cancelled"
        self._robot.notify_state_change()
        logger.info("주문 취소 완료")

    async def _handle_pick(self, action: Action):
        """화물 적재 시뮬레이션."""
        load_type = None
        for p in action.actionParameters:
            if p.key == "loadType":
                load_type = p.value
        new_load = Load(
            loadId=f"load-{action.actionId[:8]}",
            loadType=load_type or "UNKNOWN",
        )
        self._robot.loads.append(new_load)
        logger.info("화물 적재: %s", new_load.loadType)

    async def _handle_drop(self, action: Action):
        """화물 하역 시뮬레이션."""
        if self._robot.loads:
            removed = self._robot.loads.pop(0)
            logger.info("화물 하역: %s", removed.loadType)
        else:
            logger.warning("하역할 화물 없음")

    def _handle_init_position(self, action: Action):
        """위치 초기화."""
        x, y, theta, map_id = 0.0, 0.0, 0.0, ""
        for p in action.actionParameters:
            if p.key == "x":
                x = float(p.value)
            elif p.key == "y":
                y = float(p.value)
            elif p.key == "theta":
                theta = float(p.value)
            elif p.key == "mapId":
                map_id = p.value

        self._robot.position_x = x
        self._robot.position_y = y
        self._robot.position_theta = theta
        if map_id:
            self._robot.map_id = map_id
        self._robot.position_initialized = True
        logger.info("위치 초기화: (%.2f, %.2f, %.2f) map=%s", x, y, theta, map_id)

    def _handle_download_map(self, action: Action):
        """맵 다운로드 시뮬레이션."""
        map_id = ""
        map_download_url = ""
        map_version = ""
        for p in action.actionParameters:
            if p.key == "mapId":
                map_id = p.value
            elif p.key == "mapDownloadUrl":
                map_download_url = p.value
            elif p.key == "mapVersion":
                map_version = p.value

        logger.info("맵 다운로드 중: mapId=%s, version=%s, url=%s", map_id, map_version, map_download_url)

        # robot.maps 업데이트 (기존 mapId가 있으면 업데이트, 없으면 추가)
        updated = False
        for m in self._robot.maps:
            if m.mapId == map_id:
                m.mapVersion = map_version
                m.mapStatus = "ENABLED"
                updated = True
                break
        if not updated:
            self._robot.maps.append(
                MapState(mapId=map_id, mapVersion=map_version, mapStatus="ENABLED")
            )
        logger.info("맵 다운로드 완료: mapId=%s, version=%s", map_id, map_version)

    def has_blocking_action(self) -> bool:
        """HARD 블로킹 액션이 실행 중인지 확인."""
        for astate in self._robot.action_states:
            if astate.actionStatus in ("INITIALIZING", "RUNNING"):
                # 해당 액션의 blockingType 확인
                for action_id, task in self._running_tasks.items():
                    if action_id == astate.actionId:
                        return True  # 실행 중인 액션이 있으면 일단 true
        return False

    async def cancel_all(self):
        """모든 실행 중인 액션 취소."""
        for task in list(self._running_tasks.values()):
            task.cancel()
        self._running_tasks.clear()
