#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional


def parse_xml(xml_content: str) -> Optional[ET.Element]:
    """Parse XML string and return the root element."""
    xml_content = (xml_content or "").strip()
    if not xml_content:
        return None
    try:
        return ET.fromstring(xml_content)
    except ET.ParseError:
        return None


def xml_attr(elem: Optional[ET.Element], name: str) -> str:
    return str(elem.attrib.get(name, "")).strip() if elem is not None else ""


def xml_find_child(elem: Optional[ET.Element], tag: str) -> Optional[ET.Element]:
    return elem.find(tag) if elem is not None else None


def xml_find_all(elem: Optional[ET.Element], tag: str) -> list[ET.Element]:
    return list(elem.findall(tag)) if elem is not None else []


def xml_text(elem: Optional[ET.Element]) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def xml_child_text(elem: Optional[ET.Element], tag: str) -> str:
    return xml_text(xml_find_child(elem, tag))
