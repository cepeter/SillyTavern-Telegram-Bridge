"""Canonical generation recovery owner."""

from __future__ import annotations

import logging

from bridge.delivery_port import DeliveryPort
from bridge.metadata import get_meta
from bridge.operation_recovery import OperationRecovery as _OperationRecovery
from bridge.operations import begin_operation, operation_phase, record_operation
from bridge.sqlite_store import write_transaction


def _generation_operation_recovery(
    delivery_port: DeliveryPort,
) -> _OperationRecovery:
    return _OperationRecovery(
        operation_phase=lambda db, operation_id: operation_phase(
            db,
            operation_id,
        ),
        begin_operation=lambda db, operation_id, kind: begin_operation(
            db,
            operation_id,
            kind,
        ),
        record_operation=lambda db, operation_id, kind: record_operation(
            db,
            operation_id,
            kind,
        ),
        write_transaction=write_transaction,
        get_meta=lambda db, key, default="": get_meta(
            db,
            key,
            default,
        ),
        telegram_request=delivery_port.request,
        delete_outgoing_message_row=(delivery_port.delete_outgoing_message_row),
        log_info=lambda message, *args, **kwargs: logging.info(
            message,
            *args,
            **kwargs,
        ),
    )
