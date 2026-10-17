"""Home Assistant notifications through the Supervisor API (HAOS add-on only)."""

import logging
import os

import requests


SUPERVISOR_SERVICES_URL = "http://supervisor/core/api/services"
NOTIFICATION_ID = "akahu_to_budget_sync"


def _call_service(service_path, payload):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logging.error(
            "Cannot notify Home Assistant: SUPERVISOR_TOKEN is not set. "
            "The add-on needs homeassistant_api enabled."
        )
        return False
    try:
        response = requests.post(
            f"{SUPERVISOR_SERVICES_URL}/{service_path}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Home Assistant service call {service_path} failed: {e}")
        return False
    return True


def alert(title, message, notify_service=None):
    """Show a persistent HA notification, plus notify_service if configured.

    Returns True if the persistent notification was created; a failing
    notify_service is logged but does not stop the alert being recorded.
    """
    created = _call_service(
        "persistent_notification/create",
        {"notification_id": NOTIFICATION_ID, "title": title, "message": message},
    )
    if notify_service:
        domain, _, service = notify_service.partition(".")
        _call_service(f"{domain}/{service}", {"title": title, "message": message})
    return created


def clear():
    """Dismiss the persistent notification raised by alert()."""
    return _call_service(
        "persistent_notification/dismiss", {"notification_id": NOTIFICATION_ID}
    )
