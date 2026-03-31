import base64
import os
import re
from datetime import datetime
from graph_client import GraphClient, DeviceFlowRequired

DOC_ID_REGEX = r"\b([A-Z]{2,6}_[A-Za-z0-9_]+\d{8})\b"

def run_email_action(task_data: dict) -> dict:
    """Execute an Email agent action."""
    action = task_data.get("action", "")
    
    try:
        # Validate device auth triggers early implicitly
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
        if action == "read_inbox":
            return _read_inbox(client, task_data)
        elif action == "read_email":
            return _read_email(client, task_data)
        elif action == "summarize_email":
            return _summarize_email(client, task_data)
        elif action == "search_emails":
            return _search_emails(client, task_data)
        elif action == "reply_to_email":
            return _reply_to_email(client, task_data)
        elif action == "forward_email":
            return _forward_email(client, task_data)
        elif action == "send_email":
            return _send_email(client, task_data)
        elif action == "mark_email":
            return _mark_email(client, task_data)
        elif action == "move_email":
            return _move_email(client, task_data)
        elif action == "find_person_email":
            return _find_person_email(client, task_data)
        else:
            return {"status": "failed", "error": f"Unknown action: {action}"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _read_inbox(client: GraphClient, data: dict) -> dict:
    limit = int(data.get("limit", 15))
    folder = data.get("folder", "inbox")
    fm = {"inbox":"inbox","sent":"sentitems","drafts":"drafts","deleted":"deleteditems","trash":"deleteditems"}
    fp = fm.get(folder.lower(), "inbox")
    
    res = client.get(
        f"/me/mailFolders/{fp}/messages"
        f"?$select=id,subject,from,bodyPreview,receivedDateTime,isRead,hasAttachments,importance"
        f"&$orderby=receivedDateTime desc&$top={min(limit,50)}"
    )
    
    emails = []
    for m in res.get("value", []):
        s = m.get("from", {}).get("emailAddress", {})
        emails.append({
            "id": m.get("id",""), "subject": m.get("subject","(No subject)"),
            "from_name": s.get("name",""), "from_email": s.get("address",""),
            "preview": m.get("bodyPreview","")[:150],
            "date": m.get("receivedDateTime","")[:16].replace("T"," "),
            "isRead": m.get("isRead", True),
            "hasAttachments": m.get("hasAttachments", False),
            "importance": m.get("importance","normal"),
        })
    return {"status": "success", "emails": emails}

def _read_email(client: GraphClient, data: dict) -> dict:
    message_id = data.get("message_id", "")
    if not message_id: raise ValueError("message_id required")
    
    m = client.get(
        f"/me/messages/{message_id}"
        f"?$select=id,subject,from,body,receivedDateTime,importance,toRecipients,conversationId"
    )
    
    s = m.get("from", {}).get("emailAddress", {})
    to_list = [x["emailAddress"]["address"] for x in m.get("toRecipients", []) if "emailAddress" in x]
    raw  = m.get("body", {}).get("content", "")
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return {
        "status": "success",
        "email": {
            "id": m.get("id",""), "subject": m.get("subject",""),
            "from_name": s.get("name",""), "from_email": s.get("address",""),
            "to": to_list, "body": text[:4000],
            "date": m.get("receivedDateTime","")[:16].replace("T"," "),
            "importance": m.get("importance","normal"),
            "conversation_id": m.get("conversationId",""),
        }
    }

def _summarize_email(client: GraphClient, data: dict) -> dict:
    # Summarization should happen at the LLM parent level usually,
    # but since this was an agent capability, we return the email body
    # to let the parent LLM summarize, or if we really need to call ollama:
    message_id = data.get("message_id", "")
    subject = data.get("subject", "")
    from_name = data.get("from_name", "")
    from_email = data.get("from_email", "")
    body = data.get("body", "")
    
    if message_id and not body:
        email_d = _read_email(client, {"message_id": message_id}).get("email", {})
        subject = email_d.get("subject", subject)
        from_name = email_d.get("from_name", from_name)
        from_email = email_d.get("from_email", from_email)
        body = email_d.get("body", body)

    if not body and not subject:
        raise ValueError("message_id or email body required")

    return {
        "status": "success",
        "action": "Please read and summarize this email content for the user",
        "subject": subject,
        "from": f"{from_name} <{from_email}>",
        "body": body[:3000]
    }

def _search_emails(client: GraphClient, data: dict) -> dict:
    query = data.get("query", "")
    sender = data.get("sender", "")
    since = data.get("since", "")
    limit = int(data.get("limit", 10))
    
    filters = []
    if sender: filters.append(f"from/emailAddress/address eq '{sender}'")
    if since: filters.append(f"receivedDateTime ge {since}T00:00:00Z")
    params = f"?$select=id,subject,from,bodyPreview,receivedDateTime,isRead&$orderby=receivedDateTime desc&$top={min(limit,25)}"
    if query: params += f"&$search=\"{query}\""
    elif filters: params += f"&$filter={' and '.join(filters)}"
    
    res = client.get(f"/me/messages{params}")
    results = []
    for m in res.get("value",[]):
        sn = m.get("from",{}).get("emailAddress",{})
        results.append({
            "id": m.get("id",""), "subject": m.get("subject","(No subject)"),
            "from_name": sn.get("name",""), "from_email": sn.get("address",""),
            "preview": m.get("bodyPreview","")[:120],
            "date": m.get("receivedDateTime","")[:16].replace("T"," "),
            "isRead": m.get("isRead", True),
        })
    return {"status": "success", "results": results}

def _reply_to_email(client: GraphClient, data: dict) -> dict:
    message_id = data.get("message_id", "")
    body = data.get("body", "")
    reply_all = data.get("reply_all", False)
    if not message_id: raise ValueError("message_id required")
    if not body: raise ValueError("body required")
    
    ep = "replyAll" if reply_all else "reply"
    client.post(f"/me/messages/{message_id}/{ep}", {"message":{"body":{"contentType":"Text","content":body}}})
    return {"status": "success", "message": "Reply sent."}

def _forward_email(client: GraphClient, data: dict) -> dict:
    message_id = data.get("message_id", "")
    to_email = data.get("to_email", "")
    comment = data.get("comment", "")
    if not message_id: raise ValueError("message_id required")
    if not to_email: raise ValueError("to_email required")
    
    client.post(f"/me/messages/{message_id}/forward", {"comment":comment,"toRecipients":[{"emailAddress":{"address":to_email}}]})
    return {"status": "success", "message": f"Forwarded to {to_email}."}

def _send_email(client: GraphClient, data: dict) -> dict:
    to = data.get("to", "")
    subject = data.get("subject", "")
    body = data.get("body", "")
    cc = data.get("cc", "")
    if not to: raise ValueError("to required")
    if not subject: raise ValueError("subject required")
    if not body: raise ValueError("body required")
    
    msg: dict = {"subject":subject,"body":{"contentType":"Text","content":body},"toRecipients":[{"emailAddress":{"address":t.strip()}} for t in to.split(",") if t.strip()]}
    if cc: msg["ccRecipients"] = [{"emailAddress":{"address":a.strip()}} for a in cc.split(",") if a.strip()]
    client.post("/me/sendMail", {"message":msg,"saveToSentItems":True})
    return {"status": "success", "to": to, "subject": subject}

def _mark_email(client: GraphClient, data: dict) -> dict:
    message_id = data.get("message_id", "")
    is_read = data.get("is_read", True)
    flagged = data.get("flagged", False)
    if not message_id: raise ValueError("message_id required")
    
    client.patch(f"/me/messages/{message_id}", {"isRead":is_read,"flag":{"flagStatus":"flagged" if flagged else "notFlagged"}})
    return {"status": "success", "message": f"Marked as {'read' if is_read else 'unread'}."}

def _move_email(client: GraphClient, data: dict) -> dict:
    message_id = data.get("message_id", "")
    destination = data.get("destination", "archive")
    if not message_id: raise ValueError("message_id required")
    
    fm = {"archive":"archive","junk":"junkemail","spam":"junkemail","inbox":"inbox","deleted":"deleteditems","trash":"deleteditems"}
    dest = fm.get(destination.lower(),"archive")
    client.post(f"/me/messages/{message_id}/move", {"destinationId":dest})
    return {"status": "success", "message": f"Moved to {destination}."}

def _find_person_email(client: GraphClient, data: dict) -> dict:
    name = data.get("name", "")
    if "@" in name: return {"status": "success", "email":name,"displayName":name}
    res = client.get(f'/me/people?$search="{name}"&$top=10')
    results = []
    for p in res.get("value",[]):
        scored = p.get("scoredEmailAddresses") or []
        email = (scored[0].get("address") if scored else None) or p.get("userPrincipalName","")
        if email: results.append({"email":email,"displayName":p.get("displayName",email)})
    if not results: raise ValueError(f"'{name}' not found.")
    if len(results)==1: return {"status": "success", **results[0]}
    return {"status": "success", "multiple_matches":results}
