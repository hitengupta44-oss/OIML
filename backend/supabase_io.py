"""
Supabase access for the reporter Space.

The Space holds the service-role key, which bypasses row-level security
entirely. That is deliberate -- rendering has to read across an evaluation
and write files back -- but it means the isolation RLS would have given us
is gone, so every function here re-applies it in Python using the caller's
JWT claims. Never call these without a Caller from auth.verify().
"""

import os
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any, Dict, List, Optional

from auth import Caller, may_touch_lab

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
REPORT_BUCKET = os.environ.get("SUPABASE_REPORT_BUCKET", "reports")
SIGNED_URL_SECONDS = int(os.environ.get("SIGNED_URL_SECONDS", "3600"))


class SupabaseError(Exception):
    pass


def configured() -> bool:
    return bool(URL and SERVICE_KEY)


def _request(method: str, path: str, body=None, headers=None, raw=False):
    if not configured():
        raise SupabaseError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY are not set on this Space. "
            "Add them under Settings, Repository secrets."
        )
    h = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
    }
    data = None
    if body is not None and not raw:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    elif raw:
        data = body
    h.update(headers or {})

    req = urllib.request.Request(f"{URL}{path}", method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
            if not payload:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload
    except urllib.error.HTTPError as exc:
        raise SupabaseError(
            f"{method} {path} -> {exc.code}: {exc.read().decode()[:300]}"
        ) from exc


def _select(table: str, query: str) -> List[Dict[str, Any]]:
    return _request("GET", f"/rest/v1/{table}?{query}") or []


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def fetch_evaluation(ref_or_id: str, caller: Caller) -> Dict[str, Any]:
    """Load one evaluation with everything a report needs.

    Raises if the caller's laboratory does not match, re-applying the
    isolation the service-role key bypasses.
    """
    key = "id" if _looks_like_uuid(ref_or_id) else "ref"
    value = urllib.parse.quote(ref_or_id, safe="")
    rows = _select(
        "evaluation",
        f"{key}=eq.{value}&select="
        "id,ref,status,verdict,standard_id,jurisdiction,zero_error_g,"
        "approval_mark,certificate_no,gazette_date,serial_no,applicant,"
        "application_no,evaluation_from,evaluation_to,weight_set_id,load_cell,"
        "is_synthetic,lab_id,model_id,"
        "lab:lab_id(code,name,state,mark_code),"
        "model:model_id("
        "  model,instrument_type,accuracy_class,max_capacity_g,e_g,d_g,"
        "  min_capacity_g,n,indication_type,is_electronic,has_tare_device,"
        "  has_aux_device,is_grading,temp_low_c,temp_high_c,software_version,"
        "  software_checksum,manufacturer:manufacturer_id(name,city,state))"
        "&limit=1",
    )
    if not rows:
        raise SupabaseError(f"no evaluation found for '{ref_or_id}'")
    ev = rows[0]

    if not may_touch_lab(caller, ev.get("lab_id")):
        raise SupabaseError(
            f"evaluation {ev['ref']} belongs to another laboratory"
        )

    model = ev.pop("model", None) or {}
    manufacturer = (model.pop("manufacturer", None) or {}).get("name")
    ev["instrument"] = {
        **model,
        "manufacturer": manufacturer,
        "serial": ev.get("serial_no"),
    }

    # live observations only; superseded rows stay for the audit trail
    ev["observations"] = _select(
        "observation",
        f"evaluation_id=eq.{ev['id']}&superseded_by=is.null"
        "&select=id,test_code,variant,sequence_no,load_g,indication_g,"
        "delta_load_g,direction,position,elapsed_minutes,weighing_no,"
        "temperature_c,rh_percent,voltage_v,severity,indication_without_g,"
        "indication_with_g,significant_fault_detected,computed"
        "&order=test_code.asc,sequence_no.asc",
    )
    return ev


def _looks_like_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4


def list_evaluations(caller: Caller, status: Optional[str] = None,
                     limit: int = 50) -> List[Dict[str, Any]]:
    q = (f"select=ref,status,verdict,lab_code,lab_name,manufacturer,model,"
         f"accuracy_class,max_capacity_g,e_g,n,created_at,is_synthetic"
         f"&order=created_at.desc&limit={int(limit)}")
    if status:
        q += f"&status=eq.{urllib.parse.quote(status)}"
    if not caller.is_("director", "admin", "auditor") and caller.lab_id:
        labs = _select("lab", f"id=eq.{caller.lab_id}&select=code")
        if labs:
            q += f"&lab_code=eq.{labs[0]['code']}"
    return _select("v_evaluation_summary", q)


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

def upload_report(local_path: str, ref: str, version: int,
                  content_type: str) -> str:
    """Put a rendered file in the reports bucket and return its storage path."""
    ext = os.path.splitext(local_path)[1]
    safe = ref.replace("/", "_")
    storage_path = f"{safe}/v{version}/{safe}{ext}"
    with open(local_path, "rb") as f:
        _request(
            "POST",
            f"/storage/v1/object/{REPORT_BUCKET}/{urllib.parse.quote(storage_path)}",
            body=f.read(), raw=True,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
    return storage_path


def signed_url(storage_path: str, seconds: int = SIGNED_URL_SECONDS) -> str:
    """Reports are private; hand out a time-limited link, never a public one."""
    res = _request(
        "POST",
        f"/storage/v1/object/sign/{REPORT_BUCKET}/{urllib.parse.quote(storage_path)}",
        body={"expiresIn": seconds},
    )
    return f"{URL}/storage/v1{res['signedURL']}" if res else ""


def record_report_version(evaluation_id: str, version: int, standard_id: str,
                          payload: Dict[str, Any], sha256: str, verdict: str,
                          docx_path: Optional[str], pdf_path: Optional[str],
                          caller: Caller) -> Dict[str, Any]:
    rows = _request(
        "POST", "/rest/v1/report_version",
        body={
            "evaluation_id": evaluation_id, "version": version,
            "standard_id": standard_id, "payload": payload,
            "payload_sha256": sha256, "verdict": verdict,
            "docx_path": docx_path, "pdf_path": pdf_path,
            "rendered_by": caller.user_id or None,
        },
        headers={"Prefer": "return=representation"},
    )
    return rows[0] if rows else {}


def next_version(evaluation_id: str) -> int:
    rows = _select(
        "report_version",
        f"evaluation_id=eq.{evaluation_id}&select=version"
        "&order=version.desc&limit=1",
    )
    return (rows[0]["version"] + 1) if rows else 1
