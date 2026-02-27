import httpx
from typing import Dict, Any, List, Optional, Tuple


def _normalize_api_url(api_url: str) -> str:
    return (api_url or "").rstrip("/")


def _normalize_events(raw_events: Optional[List[str]]) -> List[str]:
    if not raw_events:
        return ["sms:received"]

    events: List[str] = []
    for item in raw_events:
        if not item:
            continue
        ev = str(item).strip()
        if not ev:
            continue
        if ev not in events:
            events.append(ev)
    return events or ["sms:received"]


async def _register_event(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    username: str,
    password: str,
    webhook_url: str,
    event_name: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    endpoint = f"{_normalize_api_url(api_url)}/webhooks"
    payload: Dict[str, Any] = {
        "url": webhook_url,
        "event": event_name,
    }
    if device_id:
        payload["device_id"] = device_id

    response = await client.post(
        endpoint,
        auth=httpx.BasicAuth(username=username, password=password),
        json=payload,
    )

    if response.status_code in (400, 422) and device_id:
        alt_payload = {
            "url": webhook_url,
            "event": event_name,
            "deviceId": device_id,
        }
        response = await client.post(
            endpoint,
            auth=httpx.BasicAuth(username=username, password=password),
            json=alt_payload,
        )

    response.raise_for_status()
    data = response.json() if response.content else {}
    return {
        "event": event_name,
        "id": data.get("id"),
        "response": data,
    }


async def register_cloud_webhooks(
    *,
    api_url: str,
    username: str,
    password: str,
    webhook_url: str,
    events: Optional[List[str]] = None,
    device_id: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    success: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    normalized_events = _normalize_events(events)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for event_name in normalized_events:
            try:
                result = await _register_event(
                    client,
                    api_url=api_url,
                    username=username,
                    password=password,
                    webhook_url=webhook_url,
                    event_name=event_name,
                    device_id=device_id,
                )
                success.append(result)
            except httpx.HTTPStatusError as ex:
                res = ex.response
                errors.append(
                    {
                        "event": event_name,
                        "status_code": res.status_code if res else None,
                        "message": (res.text[:500] if res is not None and res.text else str(ex)),
                    }
                )
            except Exception as ex:
                errors.append(
                    {
                        "event": event_name,
                        "status_code": None,
                        "message": str(ex),
                    }
                )

    return success, errors


async def unregister_cloud_webhooks(
    *,
    api_url: str,
    username: str,
    password: str,
    webhook_ids: List[str],
    timeout_seconds: float = 20.0,
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    endpoint_base = f"{_normalize_api_url(api_url)}/webhooks"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for webhook_id in webhook_ids:
            if not webhook_id:
                continue
            url = f"{endpoint_base}/{webhook_id}"
            try:
                res = await client.delete(url, auth=httpx.BasicAuth(username=username, password=password))
                res.raise_for_status()
            except httpx.HTTPStatusError as ex:
                response = ex.response
                errors.append(
                    {
                        "id": webhook_id,
                        "status_code": response.status_code if response else None,
                        "message": (response.text[:500] if response is not None and response.text else str(ex)),
                    }
                )
            except Exception as ex:
                errors.append(
                    {
                        "id": webhook_id,
                        "status_code": None,
                        "message": str(ex),
                    }
                )

    return errors
