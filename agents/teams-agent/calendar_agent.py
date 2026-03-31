from datetime import datetime, timedelta
import re
from graph_client import GraphClient, DeviceFlowRequired

def run_calendar_action(task_data: dict) -> dict:
    """Execute a Calendar agent action."""
    action = task_data.get("action", "")
    
    try:
        client = GraphClient(
            access_token=task_data.get("access_token"),
            refresh_token=task_data.get("refresh_token"),
        )
        _ = client.acquire_token()
    except DeviceFlowRequired as e:
        return {
            "status": "action_required",
            "type": "device_auth",
            "flow": e.flow_data,
        }

    try:
        if action == "get_calendar_events":
            return _get_calendar_events(client, task_data)
        elif action == "create_calendar_event":
            return _create_calendar_event(client, task_data)
        elif action == "check_conflicts":
            return _check_conflicts(client, task_data)
        elif action == "delete_event":
            return _delete_event(client, task_data)
        elif action == "find_person_email":
            return _find_person_email(client, task_data)
        else:
            return {"status": "failed", "error": f"Unknown calendar action: {action}"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}

def _get_calendar_events(client: GraphClient, data: dict) -> dict:
    start_iso = data.get("start_iso", "")
    end_iso = data.get("end_iso", "")
    
    if not start_iso: return {"status": "failed", "error": "start_iso is required"}
    if not end_iso:
        end_iso = (datetime.fromisoformat(start_iso[:10]) + timedelta(days=1)).isoformat()
        
    res = client.get(
        f"/me/events"
        f"?$filter=start/dateTime ge '{start_iso}' and start/dateTime lt '{end_iso}'"
        f"&$orderby=start/dateTime"
        f"&$select=id,subject,start,end,location,isAllDay,isCancelled,attendees,responseStatus,isOrganizer"
        f"&$top=50"
    )
    
    events = []
    for e in res.get("value", []):
        if e.get("isCancelled"): continue
        events.append({
            "event_id": e.get("id"),
            "subject": e.get("subject", "(No title)"),
            "start": e.get("start", {}).get("dateTime", "")[:16].replace("T", " "),
            "end": e.get("end", {}).get("dateTime", "")[:16].replace("T", " "),
            "all_day": e.get("isAllDay", False),
            "location": e.get("location", {}).get("displayName", ""),
            "response": e.get("responseStatus", {}).get("response", "unknown"),
            "organizer": "Yes" if e.get("isOrganizer") else "No"
        })
    return {"status": "success", "events": events}

def _create_calendar_event(client: GraphClient, data: dict) -> dict:
    title = data.get("title", "Event")
    start_iso = data.get("start_iso", "")
    end_iso = data.get("end_iso", "")
    all_day = data.get("all_day", False)
    attendees = data.get("attendees", [])
    description = data.get("description", "")
    
    if not start_iso: return {"status": "failed", "error": "start_iso is required"}
    if not end_iso:
        end_iso = (datetime.fromisoformat(start_iso) + timedelta(hours=1)).isoformat()
        
    body = {
        "subject": title,
        "isAllDay": all_day,
        "body": {"contentType": "text", "content": description},
    }
    
    if all_day:
        body["start"] = {"dateTime": start_iso[:10], "timeZone": "Asia/Kolkata"}
        body["end"] = {"dateTime": end_iso[:10], "timeZone": "Asia/Kolkata"}
    else:
        body["start"] = {"dateTime": start_iso, "timeZone": "Asia/Kolkata"}
        body["end"] = {"dateTime": end_iso, "timeZone": "Asia/Kolkata"}
        
    if attendees:
        body["attendees"] = [{"emailAddress": {"address": a}, "type": "required"} for a in attendees if a]
        
    res = client.post("/me/events", body)
    return {
        "status": "success",
        "title": title,
        "start": start_iso,
        "event_id": res.get("id", ""),
        "message": "Event created"
    }

def _check_conflicts(client: GraphClient, data: dict) -> dict:
    start_iso = data.get("start_iso", "")
    end_iso = data.get("end_iso", "")
    
    if not start_iso or not end_iso:
        return {"status": "failed", "error": "start_iso and end_iso required"}

    res = client.get(
        f"/me/calendarView?startDateTime={start_iso}Z&endDateTime={end_iso}Z"
        f"&$select=subject,start,end,isCancelled,showAs"
    )
    conflicts = []
    for e in res.get("value", []):
        if e.get("isCancelled") or e.get("showAs") == "free": continue
        conflicts.append({
            "subject": e.get("subject"),
            "start": e.get("start", {}).get("dateTime", "")[:16].replace("T", " "),
            "end": e.get("end", {}).get("dateTime", "")[:16].replace("T", " ")
        })
    free = len(conflicts) == 0
    return {"status": "success", "free": free, "conflicts": conflicts}

def _delete_event(client: GraphClient, data: dict) -> dict:
    event_id = data.get("event_id", "")
    if not event_id: return {"status": "failed", "error": "event_id required"}
    
    client.delete(f"/me/events/{event_id}")
    return {"status": "success", "message": "Event deleted"}

def _find_person_email(client: GraphClient, data: dict) -> dict:
    name = data.get("name", "")
    if "@" in name: return {"status": "success", "email":name,"displayName":name}
    res = client.get(f'/me/people?$search="{name}"&$top=5')
    results = []
    for p in res.get("value",[]):
        scored = p.get("scoredEmailAddresses") or []
        email = (scored[0].get("address") if scored else None) or p.get("userPrincipalName","")
        if email: results.append({"email":email,"displayName":p.get("displayName",email)})
    if not results: return {"status": "failed", "error": f"'{name}' not found."}
    if len(results)==1: return {"status": "success", **results[0]}
    return {"status": "success", "multiple_matches":results}
