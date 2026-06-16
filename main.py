"""MBDataflow entry point."""

from __future__ import annotations
import base64

import argparse
from typing import Iterable, Optional, Type

from mbdataflow.connectors import CONNECTORS
from mbdataflow.connectors.base import Connector
from mbdataflow.connectors.FlotaVehicular import FlotaV_Connector
from mbdataflow.connectors.CanBus import CanBus_Connector


def run_connectors(connector_classes: Iterable[Type[Connector]]) -> None:
    """Run the given connector classes."""
    for connector_cls in connector_classes:
        connector = connector_cls()
        connector.run()


def main(connector_name: Optional[str] = None) -> None:
    """Run MBDataflow connectors.

    Parameters
    ----------
    connector_name:
        Optional name of a specific connector to run. If ``None``, all
        available connectors are executed.
    """
    if connector_name:
        selected = [c for c in CONNECTORS if c.name == connector_name]
        if not selected:
            raise SystemExit(f"Connector '{connector_name}' not found.")
    else:
        selected = CONNECTORS
    run_connectors(selected)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MBDataflow connectors")
    parser.add_argument(
        "--connector",
        dest="connector_name",
        help="Name of a single connector to run",
    )
    return parser.parse_args()

def run_sonda_pv_connector(event, context):
    """Cloud Function entry point to run the SondaPVConnector.

    This function can be triggered by an HTTP request or a Pub/Sub message.
    """
    print("Iniciando ejecución del conector Sonda_PV...")
    try:
        connector = FlotaV_Connector()
        connector.run()
        print("Ejecución del conector Sonda_PV completada exitosamente.")
        return "OK", 200
    except Exception as e:
        print(f"Error durante la ejecución del conector: {e}")
        raise


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    args = _parse_args()
    main(args.connector_name)
