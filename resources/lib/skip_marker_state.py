# -*- coding: utf-8 -*-
from math import ceil


PROPERTY_KEYS = (
    'available',
    'type',
    'label',
    'end',
    'toast_visible',
    'hide_remaining',
    'hide_progress_frame',
)


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _progress_frame(percent):
    percent = _clamp(percent, 0, 100)
    return int(round(percent / 2.0) * 2)


def build_properties(
        marker_type,
        marker_message,
        marker_end,
        creation_time,
        progress,
        auto_hide_seconds,
        toast_visible=True):
    auto_hide_seconds = max(0, int(auto_hide_seconds or 0))
    elapsed = max(0.0, float(progress) - float(creation_time))
    remaining = max(0.0, auto_hide_seconds - elapsed)

    if auto_hide_seconds:
        percent_remaining = (remaining / auto_hide_seconds) * 100.0
    else:
        percent_remaining = 100.0

    return {
        'available': '1',
        'type': marker_type,
        'label': marker_message,
        'end': str(marker_end),
        'toast_visible': '1' if toast_visible else '',
        'hide_remaining': str(int(ceil(remaining))),
        'hide_progress_frame': str(_progress_frame(percent_remaining)),
    }


def clear_properties():
    return {key: '' for key in PROPERTY_KEYS}
