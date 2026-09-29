#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations
from typing import Any, Optional, cast

from .vitis_comp_model import Configuration, VitisComp
from .file_path_util import (
    get_file_name,
    get_relative_path,
    join_and_normalize,
    find_file,
)
from .file_util import path_exists, read_file, read_json
from .cfg_parser import get_cfg_dict, get_hls_top


def get_vitis_comp(component_location: str) -> Optional[VitisComp]:
    vitis_comp_file = join_and_normalize(component_location, "vitis-comp.json")
    if path_exists(vitis_comp_file):
        vitis_comp = read_json(vitis_comp_file)
        configuration = _get_dict_value(vitis_comp, "configuration")
        return VitisComp(
            name=_get_dict_str_value(vitis_comp, "name"),
            type=_get_dict_str_value(vitis_comp, "type"),
            configuration=Configuration(
                component_type=_get_dict_str_value(configuration, "componentType"),
                config_files=_get_dict_str_list_value(configuration, "configFiles"),
                work_dir=_get_dict_str_value(configuration, "work_dir"),
            ),
        )
    else:
        cfg_file = find_file(
            component_location, lambda p: p.name == "hls_config.cfg"
        ) or find_file(
            component_location,
            lambda p: p.name.endswith(".cfg") and bool(_read_hls_top(str(p))),
        )
        return (
            VitisComp(
                name=get_file_name(component_location),
                type="HLS",
                configuration=Configuration(
                    component_type="HLS",
                    config_files=[get_relative_path(cfg_file, component_location)],
                    work_dir=_read_hls_top(cfg_file),
                ),
            )
            if cfg_file
            else None
        )


def _read_hls_top(cfg_file: str) -> str:
    return get_hls_top(get_cfg_dict(read_file(cfg_file)))


def is_hls_component(vitis_comp: VitisComp) -> bool:
    return vitis_comp.configuration.component_type.strip().upper() == "HLS"


def get_config_files(vitis_comp: VitisComp) -> list[str]:
    return [
        config_file.strip()
        for config_file in vitis_comp.configuration.config_files
        if config_file.strip()
    ]


def get_config_file(vitis_comp: VitisComp) -> str:
    return next(iter(get_config_files(vitis_comp)), "")


def get_work_dir(vitis_comp: VitisComp) -> str:
    return vitis_comp.configuration.work_dir.strip()


def get_component_name(vitis_comp: VitisComp) -> str:
    return vitis_comp.name.strip()


def _get_dict_value(obj: Optional[Any], key: str) -> Optional[Any]:
    return cast(dict[str, Any], obj).get(key) if isinstance(obj, dict) else None


def _get_dict_str_value(obj: Optional[Any], key: str) -> str:
    value = _get_dict_value(obj, key)
    return value if isinstance(value, str) else ""


def _get_dict_str_list_value(obj: Optional[Any], key: str) -> list[str]:
    value = _get_dict_value(obj, key)
    return (
        [v for v in cast(list[Any], value) if isinstance(v, str)]
        if isinstance(value, list)
        else []
    )


def find_hls_cfg_file(component_location: str, vitis_comp: VitisComp) -> Optional[str]:
    config_files = get_config_files(vitis_comp)
    return next(
        (
            hls_cfg_file
            for hls_cfg_file in [
                join_and_normalize(component_location, config_file)
                for config_file in config_files
            ]
            if path_exists(hls_cfg_file)
        ),
        _default_hls_cfg_file(component_location),
    )


def _default_hls_cfg_file(component_location: str) -> Optional[str]:
    default = join_and_normalize(component_location, "hls_config.cfg")
    return default if path_exists(default) else None
