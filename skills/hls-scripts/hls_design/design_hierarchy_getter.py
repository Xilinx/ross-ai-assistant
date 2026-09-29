#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Optional

from .design_info_getter import get_design_info
from .design_model import Instance, LoopInstance, TopInstance
from .design_parser import to_design


def get_top_instance(solution_folder: str) -> Optional[TopInstance]:
    return to_design(get_design_info(solution_folder)) if solution_folder else None


def get_instance_path(
    instance: TopInstance | Instance | LoopInstance, topInstance: TopInstance
) -> str:
    return _calc_instance_path(instance, "", topInstance)


def _child_instance_path(
    current_instance_path: str,
    current_instance: TopInstance | Instance | LoopInstance,
) -> str:
    if isinstance(current_instance, Instance):
        return (
            current_instance_path + "." + current_instance.inst_name
            if current_instance_path
            else current_instance.inst_name
        )
    elif isinstance(current_instance, LoopInstance):
        return current_instance_path
    else:
        return ""


def _calc_instance_path(
    instance: TopInstance | Instance | LoopInstance,
    current_instance_path: str,
    current_instance: TopInstance | Instance | LoopInstance,
) -> str:
    if instance == current_instance:
        return current_instance_path
    else:
        for child_base_instance in current_instance.child_base_instances:
            instance_path = _calc_instance_path(
                instance,
                _child_instance_path(current_instance_path, current_instance),
                child_base_instance,
            )
            if instance_path:
                return instance_path
        return ""
