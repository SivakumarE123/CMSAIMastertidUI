# ============================================================
# Streamlit UI for PII Protection and Mistral OCR (FINAL)
# ============================================================

import streamlit as st
import os
from docx import Document
import json
import base64
import traceback
from io import BytesIO
from PIL import Image
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
import asyncio
from dotenv import load_dotenv
import re
import time as _time
import requests as http_requests
from urllib.parse import urlencode
import socket
import platform

load_dotenv()

MCP_URL = os.getenv("MCP_URL", "")

# ============================================================
# DEBUG INFRASTRUCTURE (remove entire block after debugging)
# ============================================================
_APP_START = _time.time()

def _dbg(msg):
    """Append timestamped message to persistent debug log (survives st.rerun)."""
    if "debug_log" not in st.session_state:
        st.session_state.debug_log = []
    entry = f"[{_time.strftime('%H:%M:%S')}] {msg}"
    st.session_state.debug_log.append(entry)
    print(entry)  # also to server console
    if len(st.session_state.debug_log) > 500:
        st.session_state.debug_log = st.session_state.debug_log[-500:]

def _safe_args(args):
    """Truncate large values (base64) for safe logging."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            out[k] = f"<{len(v)} chars>"
        else:
            out[k] = v
    return out

def _test_tcp(host, port, timeout=5):
    """Test TCP connectivity → (ok, latency_ms, error)."""
    try:
        t0 = _time.time()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, round((_time.time() - t0) * 1000), None
    except Exception as e:
        return False, 0, str(e)

# TID OAuth Configuration
TID_CLIENT_ID = os.getenv("TID_CLIENT_ID")
TID_CLIENT_SECRET = os.getenv("TID_CLIENT_SECRET")
TID_AUTH_URL = os.getenv("TID_AUTH_URL")
TID_TOKEN_URL = os.getenv("TID_TOKEN_URL")
TID_USERINFO_URL = os.getenv("TID_USERINFO_URL")
TID_REDIRECT_URI = os.getenv("TID_REDIRECT_URI")
TID_OAUTH_SCOPE = os.getenv("TID_OAUTH_SCOPE")
TID_LOGOUT_URL = os.getenv("TID_LOGOUT_URL")

st.set_page_config(page_title="PII Protection, OCR & Transcription", layout="wide")
st.title("🔐 PII Protection, OCR & Transcription Tool")


# ============================================================
# TID AUTHENTICATION HELPERS
# ============================================================
def get_tid_auth_url():
    params = urlencode({
        "scope": TID_OAUTH_SCOPE,
        "response_type": "code",
        "redirect_uri": TID_REDIRECT_URI,
        "client_id": TID_CLIENT_ID,
    })
    return f"{TID_AUTH_URL}?{params}"


# def exchange_code_for_token(code):
#     resp = http_requests.post(TID_TOKEN_URL, data={
#         "grant_type": "authorization_code",
#         "code": code,
#         "redirect_uri": TID_REDIRECT_URI,
#         "client_id": TID_CLIENT_ID,
#         "client_secret": TID_CLIENT_SECRET,      
#     }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
#     resp.raise_for_status()
#     return resp.json()

def exchange_code_for_token(code):
    resp = http_requests.post(TID_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": TID_REDIRECT_URI,
    }, auth=(TID_CLIENT_ID, TID_CLIENT_SECRET),
    headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"{resp.status_code} - {resp.text}")
    return resp.json()

def get_tid_user_info(access_token):
    resp = http_requests.get(TID_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}"
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def decode_id_token_email(id_token):
    """Extract email from ID token payload (token already obtained server-side)."""
    try:
        payload_part = id_token.split(".")[1]
        # Add padding for base64
        payload_part += "=" * (4 - len(payload_part) % 4)
        payload = json.loads(base64.b64decode(payload_part).decode("utf-8"))
        return payload.get("email", "")
    except Exception:
        return ""


# ============================================================
# MCP CALL
# ============================================================
def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    _dbg(f"MCP CALL → {tool_name}({json.dumps(_safe_args(arguments))})")
    t_start = _time.time()

    async def _call():
        _dbg(f"  MCP connecting to {MCP_URL}")
        t_conn = _time.time()
        async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
            _dbg(f"  MCP connected ({_time.time() - t_conn:.2f}s)")
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                _dbg(f"  MCP session init ({_time.time() - t_conn:.2f}s)")
                result = await session.call_tool(tool_name, arguments)
                _dbg(f"  MCP tool returned ({_time.time() - t_conn:.2f}s) isError={result.isError}")

                if result.isError:
                    error_msg = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            error_msg += content.text
                    _dbg(f"  MCP ERROR: {error_msg[:300]}")
                    raise Exception(f"MCP tool error: {error_msg}")

                for content in result.content:
                    if hasattr(content, "text"):
                        parsed = json.loads(content.text)
                        _dbg(f"  MCP RESPONSE: {json.dumps(parsed, default=str)[:300]}")
                        return parsed

                _dbg("  MCP returned EMPTY content!")
                return {}

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_call())
        _dbg(f"MCP DONE ← {tool_name} ({_time.time() - t_start:.2f}s)")
        return result
    except Exception as e:
        _dbg(f"MCP FAIL ← {tool_name} ({_time.time() - t_start:.2f}s): {e}")
        raise
    finally:
        loop.close()


# ============================================================
# HELPERS
# ============================================================
def extract_text(file):
    if file.name.endswith(".txt"):
        return file.read().decode("utf-8")

    elif file.name.endswith(".docx"):
        doc = Document(file)
        return "\n".join([para.text for para in doc.paragraphs])

    return ""


def compress_image(image_bytes):
    img = Image.open(BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    output = BytesIO()
    img.save(output, format="JPEG", quality=80)
    return output.getvalue()


def markdown_to_text(md):
    """Convert markdown links to readable text"""
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', md)


# Reusable session for Speech API polling (connection reuse)
_speech_session = http_requests.Session()
_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
if _SPEECH_KEY:
    _speech_session.headers.update({"Ocp-Apim-Subscription-Key": _SPEECH_KEY})
    print(f"[INFO] AZURE_SPEECH_KEY loaded ({len(_SPEECH_KEY)} chars) — direct polling enabled")
else:
    print("[WARN] AZURE_SPEECH_KEY not set — polling will fall back to MCP (slower)")


def check_speech_job_status(job_url):
    """Direct lightweight Speech API status check — bypasses MCP for fast polling."""
    _dbg(f"DIRECT → GET {job_url[:80]}...")
    if not _SPEECH_KEY:
        _dbg("  DIRECT SKIP: no AZURE_SPEECH_KEY")
        return None
    if not job_url:
        _dbg("  DIRECT SKIP: no job_url")
        return None
    try:
        t0 = _time.time()
        resp = _speech_session.get(job_url, timeout=15)
        elapsed = _time.time() - t0
        _dbg(f"  DIRECT HTTP {resp.status_code} ({elapsed:.2f}s)")
        if resp.status_code == 200:
            body = resp.json()
            status = body.get("status", "Unknown")
            _dbg(f"  DIRECT status={status} keys={list(body.keys())}")
            if status == "Succeeded":
                _dbg(f"  DIRECT links={json.dumps(body.get('links', {}))[:200]}")
            return status
        else:
            _dbg(f"  DIRECT ERROR: {resp.text[:300]}")
    except Exception as e:
        _dbg(f"  DIRECT EXCEPTION: {e}")
    return None


def fetch_speech_result_direct(job_url):
    """Fetch FULL transcription result directly from Speech API (bypass MCP)."""
    if not _SPEECH_KEY or not job_url:
        _dbg("DIRECT RESULT SKIP: no key or url")
        return None
    try:
        _dbg(f"DIRECT RESULT → GET {job_url[:80]}")
        t0 = _time.time()
        resp = _speech_session.get(job_url, timeout=15)
        if resp.status_code != 200:
            _dbg(f"DIRECT RESULT: job status HTTP {resp.status_code}")
            return None
        job_data = resp.json()
        status = job_data.get("status", "Unknown")
        _dbg(f"DIRECT RESULT: status={status}")
        if status != "Succeeded":
            return None

        files_url = job_data.get("links", {}).get("files", "")
        _dbg(f"DIRECT RESULT: files_url={files_url[:120]}")
        if not files_url:
            _dbg("DIRECT RESULT: no files link!")
            return None

        files_resp = _speech_session.get(files_url, timeout=15)
        if files_resp.status_code != 200:
            _dbg(f"DIRECT RESULT: files HTTP {files_resp.status_code}")
            return None

        transcription_text = ""
        for f in files_resp.json().get("values", []):
            if f.get("kind") == "Transcription":
                content_url = f.get("links", {}).get("contentUrl", "")
                _dbg(f"DIRECT RESULT: fetching content from {content_url[:100]}")
                if content_url:
                    content_resp = _speech_session.get(content_url, timeout=15)
                    if content_resp.status_code == 200:
                        content = content_resp.json()
                        phrases = content.get("combinedRecognizedPhrases", [])
                        if phrases:
                            transcription_text += phrases[0].get("display", "") + "\n"
                            _dbg(f"DIRECT RESULT: got {len(phrases[0].get('display',''))} chars")

        elapsed = _time.time() - t0
        _dbg(f"DIRECT RESULT DONE ({elapsed:.2f}s) text_len={len(transcription_text)}")
        return {
            "status": "success",
            "data": {"status": "Succeeded", "text": transcription_text.strip()}
        }
    except Exception as e:
        _dbg(f"DIRECT RESULT EXCEPTION: {e}")
        return None


def fetch_multi_result_direct(job_url):
    """Fetch FULL batch transcription result directly from Speech API (bypass MCP)."""
    if not _SPEECH_KEY or not job_url:
        _dbg("DIRECT MULTI SKIP: no key or url")
        return None
    try:
        _dbg(f"DIRECT MULTI → GET {job_url[:80]}")
        t0 = _time.time()
        resp = _speech_session.get(job_url, timeout=15)
        if resp.status_code != 200:
            _dbg(f"DIRECT MULTI: job HTTP {resp.status_code}")
            return None
        job_data = resp.json()
        status = job_data.get("status", "Unknown")
        if status != "Succeeded":
            return None

        files_url = job_data.get("links", {}).get("files", "")
        if not files_url:
            return None

        files_resp = _speech_session.get(files_url, timeout=15)
        if files_resp.status_code != 200:
            return None

        file_results = []
        total_text = ""
        for f in files_resp.json().get("values", []):
            if f.get("kind") == "Transcription":
                content_url = f.get("links", {}).get("contentUrl", "")
                fname = f.get("name", "file")
                if content_url:
                    content_resp = _speech_session.get(content_url, timeout=15)
                    if content_resp.status_code == 200:
                        content = content_resp.json()
                        phrases = content.get("combinedRecognizedPhrases", [])
                        text = phrases[0].get("display", "") if phrases else ""
                        file_results.append({"name": fname, "status": "completed", "text": text})
                        total_text += text + "\n"
                        _dbg(f"DIRECT MULTI: {fname} → {len(text)} chars")

        elapsed = _time.time() - t0
        _dbg(f"DIRECT MULTI DONE ({elapsed:.2f}s) files={len(file_results)}")
        return {
            "status": "success",
            "data": {
                "status": "Succeeded",
                "files": file_results,
                "total_text": total_text.strip(),
                "completed_count": len(file_results),
                "total_count": len(file_results)
            }
        }
    except Exception as e:
        _dbg(f"DIRECT MULTI EXCEPTION: {e}")
        return None


# ============================================================
# SESSION STATE
# ============================================================
if "ocr_result" not in st.session_state:
    st.session_state.ocr_result = None

if "pii_result" not in st.session_state:
    st.session_state.pii_result = None

if "pii_uploader_key" not in st.session_state:
    st.session_state.pii_uploader_key = 0

if "ocr_uploader_key" not in st.session_state:
    st.session_state.ocr_uploader_key = 0

if "video_result" not in st.session_state:
    st.session_state.video_result = None

if "video_uploader_key" not in st.session_state:
    st.session_state.video_uploader_key = 0

if "video_submit_time" not in st.session_state:
    st.session_state.video_submit_time = None

if "video_polling" not in st.session_state:
    st.session_state.video_polling = False

if "video_poll_count" not in st.session_state:
    st.session_state.video_poll_count = 0

if "transcription_status" not in st.session_state:
    st.session_state.transcription_status = None

if "multi_result" not in st.session_state:
    st.session_state.multi_result = None

if "multi_status" not in st.session_state:
    st.session_state.multi_status = None

if "multi_uploader_key" not in st.session_state:
    st.session_state.multi_uploader_key = 0

if "multi_polling" not in st.session_state:
    st.session_state.multi_polling = False

if "multi_poll_count" not in st.session_state:
    st.session_state.multi_poll_count = 0

if "multi_submit_time" not in st.session_state:
    st.session_state.multi_submit_time = None

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "user_email" not in st.session_state:
    st.session_state.user_email = None

if "user_name" not in st.session_state:
    st.session_state.user_name = None

if "debug_log" not in st.session_state:
    st.session_state.debug_log = []


# ============================================================
# TID AUTHENTICATION FLOW
# ============================================================
query_params = st.query_params

if "code" in query_params and not st.session_state.authenticated:
    try:
        token_data = exchange_code_for_token(query_params["code"])
        if "access_token" in token_data:
            user_info = get_tid_user_info(token_data["access_token"])
            st.session_state.authenticated = True
            st.session_state.user_email = user_info.get("email", "")
            st.session_state.user_name = user_info.get("name", "")
            st.query_params.clear()
            st.rerun()
        elif "id_token" in token_data:
            email = decode_id_token_email(token_data["id_token"])
            st.session_state.authenticated = True
            st.session_state.user_email = email
            st.query_params.clear()
            st.rerun()
        else:
            st.error("Authentication failed: No token received")
    except Exception as e:
        st.error(f"Authentication failed: {e}")

if not st.session_state.authenticated:
    # Auto-redirect to TID login page
    auth_url = get_tid_auth_url()
    st.markdown(
        f'<meta http-equiv="refresh" content="0;url={auth_url}">',
        unsafe_allow_html=True
    )
    st.info("Redirecting to Trimble Identity login...")
    st.stop()

# Sidebar: user info and sign out
with st.sidebar:
    st.markdown(f"👤 **{st.session_state.user_name or st.session_state.user_email}**")
    if st.button("🚪 Sign Out"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        logout_url = f"{TID_LOGOUT_URL}?client_id={TID_CLIENT_ID}&post_logout_redirect_uri={TID_REDIRECT_URI}"
        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={logout_url}">',
            unsafe_allow_html=True
        )
        st.stop()


# ============================================================
# DEBUG DASHBOARD (sidebar — remove after debugging)
# ============================================================
with st.sidebar:
    with st.expander("🐛 DEBUG DASHBOARD", expanded=False):
        # --- ENVIRONMENT ---
        st.markdown("#### Environment")
        _env_items = [
            ("Platform", platform.platform()),
            ("Python", platform.python_version()),
            ("MCP_URL", MCP_URL or "NOT SET"),
            ("SPEECH_KEY", f"SET ({len(_SPEECH_KEY)} chars)" if _SPEECH_KEY else "NOT SET"),
            ("SPEECH_REGION", os.getenv("AZURE_SPEECH_REGION", "NOT SET")),
            ("SPEECH_API_VER", os.getenv("AZURE_SPEECH_API_VERSION", "NOT SET")),
            ("STORAGE_ACCT", os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "NOT SET")),
            ("STORAGE_KEY", f"SET ({len(os.getenv('AZURE_STORAGE_ACCOUNT_KEY',''))} chars)" if os.getenv("AZURE_STORAGE_ACCOUNT_KEY") else "NOT SET"),
            ("BLOB_CONTAINER", os.getenv("AZURE_BLOB_CONTAINER", "NOT SET")),
            ("TID_CLIENT_ID", "SET" if TID_CLIENT_ID else "NOT SET"),
        ]
        for _lbl, _val in _env_items:
            st.text(f"{_lbl}: {_val}")

        st.markdown("---")
        st.markdown("#### Connectivity Tests")

        if st.button("Test MCP", key="dbg_mcp"):
            _dbg("=== MCP CONNECTIVITY TEST ===")
            try:
                from urllib.parse import urlparse
                _p = urlparse(MCP_URL)
                _h, _pt = _p.hostname, _p.port or (443 if _p.scheme == "https" else 80)
                _dbg(f"TCP → {_h}:{_pt}")
                _ok, _ms, _err = _test_tcp(_h, _pt)
                if _ok:
                    _dbg(f"TCP OK ({_ms}ms)")
                    st.success(f"TCP to {_h}:{_pt} OK ({_ms}ms)")
                else:
                    _dbg(f"TCP FAIL: {_err}")
                    st.error(f"TCP fail: {_err}")
                t0 = _time.time()
                _r = call_mcp_tool("transcription_status", {"job_url": "https://test.invalid", "email": st.session_state.user_email or "test"})
                _dbg(f"MCP test call OK ({_time.time()-t0:.2f}s): {json.dumps(_r,default=str)[:200]}")
                st.info(f"MCP response ({_time.time()-t0:.2f}s): {json.dumps(_r,default=str)[:150]}")
            except Exception as _e:
                _dbg(f"MCP test FAIL: {_e}\n{traceback.format_exc()}")
                st.error(f"MCP fail: {_e}")

        if st.button("Test Speech API", key="dbg_speech"):
            _dbg("=== SPEECH API CONNECTIVITY TEST ===")
            _region = os.getenv("AZURE_SPEECH_REGION", "")
            if not _SPEECH_KEY or not _region:
                _dbg(f"SKIP: key={'set' if _SPEECH_KEY else 'missing'} region={_region or 'missing'}")
                st.error("Missing AZURE_SPEECH_KEY or AZURE_SPEECH_REGION")
            else:
                try:
                    _api_v = os.getenv("AZURE_SPEECH_API_VERSION", "2024-11-15")
                    _turl = f"https://{_region}.api.cognitive.microsoft.com/speechtotext/transcriptions?api-version={_api_v}&top=1"
                    _dbg(f"GET {_turl}")
                    t0 = _time.time()
                    _resp = _speech_session.get(_turl, timeout=10)
                    _dbg(f"HTTP {_resp.status_code} ({_time.time()-t0:.2f}s)")
                    _dbg(f"Headers: {dict(list(_resp.headers.items())[:10])}")
                    _dbg(f"Body[0:300]: {_resp.text[:300]}")
                    if _resp.status_code == 200:
                        _jobs = _resp.json().get("values", [])
                        st.success(f"Speech API OK — {len(_jobs)} recent job(s)")
                        if _jobs:
                            _j = _jobs[0]
                            _dbg(f"Latest job: status={_j.get('status')} name={_j.get('displayName')} self={_j.get('self','')[:100]}")
                            st.info(f"Latest: {_j.get('displayName','')} — {_j.get('status','')}")
                    else:
                        st.error(f"HTTP {_resp.status_code}: {_resp.text[:200]}")
                except Exception as _e:
                    _dbg(f"Speech test FAIL: {_e}")
                    st.error(f"Speech API fail: {_e}")

        _test_job = st.text_input("Test job URL:", key="dbg_job_url", placeholder="Paste speech job URL")
        if _test_job and st.button("Check Job", key="dbg_check_job"):
            _dbg(f"=== MANUAL JOB CHECK: {_test_job[:100]} ===")
            _ds = check_speech_job_status(_test_job)
            st.info(f"Direct: {_ds}")
            try:
                _mr = call_mcp_tool("transcription_status", {"job_url": _test_job, "email": st.session_state.user_email or "test"})
                st.info(f"MCP: {json.dumps(_mr,default=str)[:300]}")
            except Exception as _e:
                st.error(f"MCP: {_e}")

        st.markdown("---")
        st.markdown("#### Session State")
        _sk_list = ["video_polling", "video_poll_count", "video_submit_time",
                    "multi_polling", "multi_poll_count", "multi_submit_time"]
        for _sk in _sk_list:
            st.text(f"{_sk}: {st.session_state.get(_sk)}")
        for _sk in ["video_result", "transcription_status", "multi_result", "multi_status"]:
            _v = st.session_state.get(_sk)
            if _v:
                st.text(f"{_sk}: {json.dumps(_v,default=str)[:200]}")
            else:
                st.text(f"{_sk}: None")

        st.markdown("---")
        _log = st.session_state.get("debug_log", [])
        st.markdown(f"#### Debug Log ({len(_log)} entries)")
        _c1, _c2 = st.columns(2)
        with _c1:
            if st.button("Clear", key="dbg_clear"):
                st.session_state.debug_log = []
                st.rerun()
        with _c2:
            st.download_button("Download", "\n".join(_log) or "(empty)", "debug_log.txt", key="dbg_dl")
        if _log:
            st.code("\n".join(_log[-150:]), language="text")
        else:
            st.caption("(no log entries yet)")


# ============================================================
# TABS
# ============================================================
tab_pii, tab_ocr, tab_video, tab_multi = st.tabs(["🛡️ PII Protection", "📄 Mistral OCR", "🎬 Video Transcription", "🌐 Multi-Source Transcription"])


# ============================================================
# TAB 1: PII
# ============================================================
with tab_pii:
    st.header("Detect & Anonymize PII")

    if st.button("🔄 Refresh", key="refresh_pii"):
        st.session_state.pii_result = None
        st.session_state.pii_uploader_key += 1
        st.rerun()

    pii_file = st.file_uploader("Upload .txt or .docx", type=["txt", "docx"], key=f"pii_upload_{st.session_state.pii_uploader_key}")

    use_deny = st.checkbox("Use Custom Deny List")

    deny_input = st.text_area(
        "Optional Deny List JSON",
        height=120,
        disabled=not use_deny
    )

    if st.button("🚀 Process PII"):

        if not pii_file:
            st.error("Upload a file")
            st.stop()

        text = extract_text(pii_file)

        if not text.strip():
            st.error("Empty file")
            st.stop()

        try:
            deny_dict = json.loads(deny_input) if use_deny and deny_input else {}

            result = call_mcp_tool("protect_multi", {
                "text": text,
                "deny_lists": json.dumps(deny_dict),
                "email": st.session_state.user_email
            })

            st.session_state.pii_result = result

        except Exception as e:
            st.error(str(e))
            st.code(traceback.format_exc())

    # DISPLAY
    if st.session_state.pii_result:
        result = st.session_state.pii_result

        if result.get("status") == "unauthorized":
            st.error("🔒 " + result.get("error", "Access Denied"))
        elif "error" in result and "original" not in result:
            st.error("❌ PII Processing Failed: " + result.get("error", "Unknown error"))
        else:
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("📄 Original")
                st.text_area("", result.get("original", ""), height=400)

            with col2:
                st.subheader("🔒 Anonymized")
                st.text_area("", result.get("anonymized", ""), height=400)

            # DOWNLOADS
            st.download_button("📄 Download Text", result.get("anonymized", ""), "pii.txt")

            st.download_button(
                "📥 Download JSON",
                data=json.dumps(
                    {
                        "anonymized_text": result.get("anonymized", "")
                    },
                    indent=2
                ),
                file_name="pii.json",
                mime="application/json"
            )

# ============================================================
# TAB 2: OCR
# ============================================================
with tab_ocr:
    st.header("Extract Text from Documents")

    if st.button("🔄 Refresh", key="refresh_ocr"):
        st.session_state.ocr_result = None
        st.session_state.ocr_uploader_key += 1
        st.rerun()

    ocr_file = st.file_uploader(
        "Upload PDF / Image / TXT",
        type=["pdf", "png", "jpg", "jpeg", "txt"],
        key=f"ocr_upload_{st.session_state.ocr_uploader_key}"
    )

    if ocr_file:
        col1, col2 = st.columns([1, 2])

        with col1:
            if "image" in ocr_file.type:
                st.image(ocr_file, use_container_width=True)
            else:
                st.info(ocr_file.name)

        with col2:
            if st.button("🚀 Run OCR"):

                try:
                    file_bytes = ocr_file.getvalue()

                    ext = ocr_file.name.split(".")[-1].lower()
                    mime_map = {
                        "pdf": "application/pdf",
                        "txt": "text/plain",
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg"
                    }

                    mime_type = mime_map.get(ext)

                    if ext in ["png", "jpg", "jpeg"]:
                        file_bytes = compress_image(file_bytes)
                        mime_type = "image/jpeg"

                    b64 = base64.b64encode(file_bytes).decode()

                    with st.spinner("Uploading and processing OCR..."):
                        result = call_mcp_tool("mistral_ocr", {
                            "file_base64": b64,
                            "mime_type": mime_type,
                            "email": st.session_state.user_email
                        })

                    st.session_state.ocr_result = result

                except Exception as e:
                    st.error(str(e))
                    st.code(traceback.format_exc())

    # DISPLAY RESULT
    if st.session_state.ocr_result:
        result = st.session_state.ocr_result

        if result.get("status") == "success":
            data = result.get("data", {})

            pages = data.get("pages") or data.get("data", {}).get("pages", [])

            st.success("✅ OCR Completed")

            if isinstance(pages, list) and len(pages) > 0:

                for page in pages:
                    with st.expander(f"📄 Page {page.get('index', 0)}", expanded=True):
                        # Render markdown content
                        st.markdown(
                            page.get("markdown", ""),
                            unsafe_allow_html=True
                        )

                        # Render hyperlinks if present as separate field
                        links = page.get("links") or page.get("hyperlinks") or []
                        if links:
                            st.markdown("---")
                            st.markdown("**🔗 Hyperlinks:**")
                            for link in links:
                                if isinstance(link, dict):
                                    text = link.get("text") or link.get("title") or ""
                                    url = link.get("url") or link.get("href") or link.get("uri") or ""
                                    if url:
                                        st.markdown(f"- [{text or url}]({url})", unsafe_allow_html=True)
                                elif isinstance(link, str):
                                    st.markdown(f"- [{link}]({link})", unsafe_allow_html=True)

                # TEXT OUTPUT — full content matching JSON download
                text_output = json.dumps(result, indent=2, default=str)

            else:
                text_output = data.get("text") or data.get("data", {}).get("text", "")
                st.markdown(text_output)

            # DOWNLOADS
            st.download_button("📄 Download Text", text_output, "ocr.txt")

            st.download_button(
                "📥 Download JSON",
                json.dumps(result, indent=2, default=str),
                "ocr.json"
            )

        elif result.get("status") == "unauthorized":
            st.error("🔒 " + result.get("error", "Access Denied"))
        else:
            st.error("❌ OCR Failed: " + result.get("error", "Unknown error"))

# ============================================================
# TAB 3: VIDEO TRANSCRIPTION
# ============================================================
with tab_video:
    st.header("Video / Audio Transcription")

    if st.button("🔄 Refresh", key="refresh_video"):
        st.session_state.video_result = None
        st.session_state.transcription_status = None
        st.session_state.video_uploader_key += 1
        st.session_state.video_submit_time = None
        st.session_state.video_polling = False
        st.session_state.video_poll_count = 0
        st.rerun()

    video_file = st.file_uploader(
        "Upload Video or Audio",
        type=["mp4", "mov", "mkv"],
        key=f"video_upload_{st.session_state.video_uploader_key}"
    )

    if video_file:
        st.info(f"📁 {video_file.name} ({video_file.size / (1024*1024):.1f} MB)")

        if st.button("🚀 Start Transcription"):
            try:
                file_bytes = video_file.getvalue()
                b64 = base64.b64encode(file_bytes).decode()

                with st.spinner("Uploading and submitting transcription job..."):
                    result = call_mcp_tool("video_transcribe", {
                        "file_base64": b64,
                        "filename": video_file.name,
                        "email": st.session_state.user_email
                    })

                st.session_state.video_result = result
                st.session_state.transcription_status = None
                st.session_state.video_submit_time = _time.time()
                st.session_state.video_polling = True
                st.session_state.video_poll_count = 0
                _dbg(f"TAB3 JOB SUBMITTED: url={result.get('speech_job_url','')[:100]} filename={result.get('filename','')}")
                st.rerun()

            except Exception as e:
                st.error(str(e))
                st.code(traceback.format_exc())

    # DISPLAY SUBMISSION RESULT
    if st.session_state.video_result:
        result = st.session_state.video_result

        if result.get("status") == "success":
            st.success("✅ Transcription job submitted!")
            video_filename = result.get("filename", "video")

            job_url = result.get("speech_job_url", "")
            if job_url:
                # Auto-poll if polling is active
                if st.session_state.get("video_polling", False):
                    st.session_state.video_poll_count = st.session_state.get("video_poll_count", 0) + 1
                    poll_count = st.session_state.video_poll_count
                    _dbg(f"=== TAB3 POLL #{poll_count} ===")

                    debug_lines = []
                    debug_lines.append(f"🕐 **Poll at:** {_time.strftime('%H:%M:%S')} (cycle #{poll_count})")
                    debug_lines.append(f"🔑 **AZURE_SPEECH_KEY:** {'✅ SET (' + str(len(_SPEECH_KEY)) + ' chars)' if _SPEECH_KEY else '❌ NOT SET — add to Azure App Settings!'}")
                    debug_lines.append(f"🌐 **MCP_URL:** `{MCP_URL}`")
                    debug_lines.append(f"🔗 **job_url:** `{job_url[:100]}...`")

                    # Fast direct API check first
                    t_direct_start = _time.time()
                    job_status = check_speech_job_status(job_url)
                    t_direct = _time.time() - t_direct_start
                    debug_lines.append(f"📡 **Direct API check:** `{job_status}` ({t_direct:.2f}s)")

                    # Fallback to MCP if direct check unavailable
                    if job_status is None:
                        debug_lines.append("⚠️ Direct check returned None — falling back to MCP...")
                        t_mcp_start = _time.time()
                        try:
                            fallback = call_mcp_tool("transcription_status", {
                                "job_url": job_url,
                                "email": st.session_state.user_email
                            })
                            t_mcp = _time.time() - t_mcp_start
                            debug_lines.append(f"🔄 **MCP raw response:** `{json.dumps(fallback)[:300]}` ({t_mcp:.2f}s)")
                            if isinstance(fallback, dict) and fallback.get("status") == "success":
                                inner = fallback.get("data")
                                if isinstance(inner, dict) and "status" in inner:
                                    job_status = inner["status"]
                                    if job_status == "Succeeded":
                                        st.session_state.transcription_status = fallback
                                    debug_lines.append(f"🏷️ **MCP extracted status:** `{job_status}`")
                                else:
                                    debug_lines.append(f"🚨 **MCP data is null/invalid:** `{inner}` — will retry")
                                    job_status = None  # keep as None, don't assume Running
                            elif isinstance(fallback, dict) and fallback.get("status") == "failed":
                                debug_lines.append(f"🚨 **MCP returned failed:** `{fallback.get('error', '')}`")
                                job_status = None
                        except Exception as ex:
                            t_mcp = _time.time() - t_mcp_start
                            debug_lines.append(f"❌ **MCP error:** `{ex}` ({t_mcp:.2f}s)")

                    debug_lines.append(f"✅ **Final job_status:** `{job_status}`")
                    _dbg(f"TAB3 FINAL: {job_status}")

                    elapsed = _time.time() - st.session_state.video_submit_time if st.session_state.video_submit_time else 0
                    mins, secs = divmod(int(elapsed), 60)
                    _dbg(f"TAB3 elapsed: {mins}m{secs}s")

                    # Show debug panel
                    with st.expander(f"🐛 DEBUG — Poll #{poll_count} (remove after debugging)", expanded=True):
                        for line in debug_lines:
                            st.markdown(line)

                    if job_status == "Succeeded":
                        # Fetch full result — try DIRECT first, fall back to MCP
                        if not st.session_state.transcription_status:
                            _dbg("TAB3 FETCHING FULL RESULT...")
                            # 1) Try direct Speech API (fast, reliable)
                            direct_result = fetch_speech_result_direct(job_url)
                            if direct_result:
                                st.session_state.transcription_status = direct_result
                                _dbg("TAB3 RESULT VIA DIRECT API ✅")
                            else:
                                # 2) Fall back to MCP
                                _dbg("TAB3 DIRECT FAILED — trying MCP...")
                                try:
                                    mcp_result = call_mcp_tool("transcription_status", {
                                        "job_url": job_url,
                                        "email": st.session_state.user_email
                                    })
                                    mcp_data = mcp_result.get("data") if isinstance(mcp_result, dict) else None
                                    if isinstance(mcp_data, dict) and mcp_data.get("status") == "Succeeded":
                                        st.session_state.transcription_status = mcp_result
                                        _dbg("TAB3 RESULT VIA MCP ✅")
                                    else:
                                        _dbg(f"TAB3 MCP ALSO INCOMPLETE — retrying: {json.dumps(mcp_result,default=str)[:200]}")
                                        st.warning("⚠️ Both direct API and MCP returned incomplete data — retrying...")
                                        _time.sleep(5)
                                        st.rerun()
                                except Exception as e:
                                    _dbg(f"TAB3 MCP RESULT ERROR: {e}")
                                    st.error(f"Error fetching result: {e}")
                        st.session_state.video_polling = False
                        _dbg("TAB3 POLLING STOPPED — Succeeded")
                        st.rerun()
                    elif job_status in ("Failed", "Cancelled"):
                        st.session_state.video_polling = False
                        _dbg(f"TAB3 POLLING STOPPED — {job_status}")
                        st.error(f"❌ Job {job_status}")
                    elif poll_count >= 120:
                        st.session_state.video_polling = False
                        _dbg("TAB3 POLLING STOPPED — 120 cycle limit")
                        st.error("❌ Polling stopped after 120 cycles (~10 min). Check debug info above.")
                    elif job_status is None:
                        _dbg(f"TAB3 STATUS UNKNOWN (None) — both direct & MCP failed, retrying...")
                        st.warning("⚠️ Could not determine job status (both direct API & MCP returned nothing). Retrying...")
                        st.markdown(f"**⏱ Elapsed:** {mins}m {secs}s")
                        st.progress(0.1, text=f"⏳ {video_filename} — waiting for status...")
                        _time.sleep(5)
                        st.rerun()
                    else:
                        _dbg(f"TAB3 CONTINUE — status={job_status} sleeping 5s")
                        st.markdown(f"**⏱ Elapsed:** {mins}m {secs}s")
                        st.progress(0.1, text=f"⏳ {video_filename} — video processing")
                        _time.sleep(5)
                        st.rerun()

        elif result.get("status") == "unauthorized":
            st.error("🔒 " + result.get("error", "Access Denied"))
        else:
            st.error("❌ Transcription Failed: " + result.get("error", "Unknown error"))

    # DISPLAY TRANSCRIPTION STATUS
    if st.session_state.transcription_status:
        status = st.session_state.transcription_status

        # Debug: show what was stored
        with st.expander("🐛 DEBUG — Stored transcription_status (remove after debugging)", expanded=False):
            st.code(json.dumps(status, indent=2, default=str)[:1000])

        if status.get("status") == "success":
            data = status.get("data") or {}
            job_status = data.get("status", "Unknown") if isinstance(data, dict) else "Unknown"

            if job_status == "Succeeded":
                transcribed_text = data.get("text", "")

                # Total elapsed time
                elapsed = 0
                if st.session_state.video_submit_time:
                    elapsed = _time.time() - st.session_state.video_submit_time
                mins, secs = divmod(int(elapsed), 60)

                st.success(f"✅ Transcription Complete in **{mins}m {secs}s**!")
                st.progress(1.0, text="✅ Video processing completed")

                st.subheader("📝 Transcribed Text")
                st.text_area("", transcribed_text, height=400)

                st.download_button("📄 Download Text", transcribed_text, "transcription.txt")

                st.download_button(
                    "📥 Download JSON",
                    json.dumps({"transcribed_text": transcribed_text}, indent=2),
                    "transcription.json",
                    mime="application/json"
                )
            elif job_status in ("Failed", "Cancelled"):
                st.error(f"❌ Job {job_status}")
            else:
                st.info(f"📋 Job Status: **{job_status}**")

        elif status.get("status") == "unauthorized":
            st.error("🔒 " + status.get("error", "Access Denied"))
        else:
            st.error("❌ Status check failed: " + status.get("error", "Unknown error"))

# ============================================================
# TAB 4: MULTI-SOURCE BATCH TRANSCRIPTION
# ============================================================
with tab_multi:
    st.header("Multi-Source Batch Transcription")
    st.caption("Upload multiple files from different sources — they are transcribed in one batch job")

    if st.button("🔄 Refresh", key="refresh_multi"):
        st.session_state.multi_result = None
        st.session_state.multi_status = None
        st.session_state.multi_uploader_key += 1
        st.session_state.multi_polling = False
        st.session_state.multi_submit_time = None
        st.session_state.multi_poll_count = 0
        st.rerun()

    # ---- SOURCES SECTION ----
    st.subheader("📁 File Uploads")
    multi_files = st.file_uploader(
        "Upload multiple video/audio files",
        type=["mp4", "mov", "mkv", "wav", "mp3", "flac", "ogg"],
        accept_multiple_files=True,
        key=f"multi_upload_{st.session_state.multi_uploader_key}"
    )

    st.subheader("🔗 Azure Blob URLs")
    blob_urls_input = st.text_area(
        "Paste Azure Blob URLs (one per line, with SAS token)",
        height=100,
        key="multi_blob_urls"
    )

    st.subheader("📂 Google Drive URLs")
    gdrive_urls_input = st.text_area(
        "Paste Google Drive file URLs (one per line)",
        height=80,
        key="multi_gdrive_urls"
    )
    gdrive_creds_raw = st.text_area(
        "Google OAuth Credentials JSON (shared for all Drive files)",
        height=100,
        key="multi_gdrive_creds",
        help="Paste your Google OAuth credentials JSON. It will be encrypted before sending."
    )
    multi_creds_encrypted = ""
    if gdrive_creds_raw and gdrive_urls_input.strip():
        try:
            enc_result = call_mcp_tool("encrypt_user_secret", {
                "plain_text": gdrive_creds_raw,
                "email": st.session_state.user_email
            })
            if enc_result.get("status") == "success":
                multi_creds_encrypted = enc_result["encrypted"]
                st.success("🔒 Credentials encrypted")
            else:
                st.error(enc_result.get("error", "Encryption failed"))
        except Exception as e:
            st.error(f"Encryption error: {e}")

    # ---- BUILD SOURCES LIST ----
    if st.button("🚀 Start Batch Transcription", key="multi_start"):
        sources = []

        # File uploads
        if multi_files:
            for f in multi_files:
                b64 = base64.b64encode(f.getvalue()).decode()
                sources.append({
                    "source_type": "file_upload",
                    "data": b64,
                    "filename": f.name,
                    "creds_encrypted": ""
                })

        # Blob URLs
        if blob_urls_input.strip():
            for line in blob_urls_input.strip().splitlines():
                url = line.strip()
                if url:
                    sources.append({
                        "source_type": "blob_url",
                        "data": url,
                        "filename": url.split("/")[-1].split("?")[0] or "blob_file",
                        "creds_encrypted": ""
                    })

        # Google Drive
        if gdrive_urls_input.strip():
            for line in gdrive_urls_input.strip().splitlines():
                url = line.strip()
                if url:
                    sources.append({
                        "source_type": "gdrive",
                        "data": url,
                        "filename": f"gdrive_{url[-10:]}",
                        "creds_encrypted": multi_creds_encrypted
                    })

        if not sources:
            st.error("No files or URLs provided")
        else:
            st.info(f"📦 Submitting **{len(sources)}** file(s) for batch transcription...")
            try:
                with st.spinner(f"Uploading {len(sources)} file(s) and submitting batch job..."):
                    result = call_mcp_tool("multi_transcribe", {
                        "sources_json": json.dumps(sources),
                        "email": st.session_state.user_email
                    })
                st.session_state.multi_result = result
                st.session_state.multi_status = None
                st.session_state.multi_polling = True
                st.session_state.multi_submit_time = _time.time()
                st.session_state.multi_poll_count = 0
                st.rerun()
            except Exception as e:
                st.error(str(e))
                st.code(traceback.format_exc())

    # ---- DISPLAY SUBMISSION RESULT ----
    if st.session_state.multi_result:
        result = st.session_state.multi_result

        if result.get("status") == "success":
            total = result.get("total", 0)
            uploaded = result.get("uploaded", 0)
            failed = result.get("failed", 0)

            st.success(f"✅ Batch job submitted — **{uploaded}/{total}** files uploaded")

            if failed > 0:
                st.warning(f"⚠️ {failed} file(s) failed to upload")

            # Show per-file upload status
            files_info = result.get("files", [])
            if files_info:
                with st.expander(f"📋 Upload Details ({len(files_info)} files)", expanded=False):
                    for i, fi in enumerate(files_info):
                        icon = "✅" if fi["status"] == "uploaded" else "❌"
                        st.markdown(f"{icon} **{fi['name']}** — {fi['status']}" +
                                    (f" ({fi['error']})" if fi.get("error") else ""))

            # ---- AUTO-POLL STATUS ----
            job_url = result.get("speech_job_url", "")
            if job_url:
                if st.session_state.get("multi_polling", False):
                    st.session_state.multi_poll_count = st.session_state.get("multi_poll_count", 0) + 1
                    poll_count = st.session_state.multi_poll_count
                    _dbg(f"=== TAB4 POLL #{poll_count} ===")

                    debug_lines = []
                    debug_lines.append(f"🕐 **Poll at:** {_time.strftime('%H:%M:%S')} (cycle #{poll_count})")
                    debug_lines.append(f"🔑 **AZURE_SPEECH_KEY:** {'✅ SET (' + str(len(_SPEECH_KEY)) + ' chars)' if _SPEECH_KEY else '❌ NOT SET — add to Azure App Settings!'}")
                    debug_lines.append(f"🌐 **MCP_URL:** `{MCP_URL}`")
                    debug_lines.append(f"🔗 **job_url:** `{job_url[:100]}...`")

                    # Fast direct API check first
                    t_direct_start = _time.time()
                    job_status = check_speech_job_status(job_url)
                    t_direct = _time.time() - t_direct_start
                    debug_lines.append(f"📡 **Direct API check:** `{job_status}` ({t_direct:.2f}s)")

                    # Fallback to MCP if direct check unavailable
                    if job_status is None:
                        debug_lines.append("⚠️ Direct check returned None — falling back to MCP...")
                        t_mcp_start = _time.time()
                        try:
                            fallback = call_mcp_tool("multi_transcription_status", {
                                "job_url": job_url,
                                "email": st.session_state.user_email
                            })
                            t_mcp = _time.time() - t_mcp_start
                            debug_lines.append(f"🔄 **MCP raw response:** `{json.dumps(fallback)[:300]}` ({t_mcp:.2f}s)")
                            if isinstance(fallback, dict) and fallback.get("status") == "success":
                                inner = fallback.get("data")
                                if isinstance(inner, dict) and "status" in inner:
                                    job_status = inner["status"]
                                    if job_status == "Succeeded":
                                        st.session_state.multi_status = fallback
                                    debug_lines.append(f"🏷️ **MCP extracted status:** `{job_status}`")
                                else:
                                    debug_lines.append(f"🚨 **MCP data is null/invalid:** `{inner}` — will retry")
                                    job_status = None
                            elif isinstance(fallback, dict) and fallback.get("status") == "failed":
                                debug_lines.append(f"🚨 **MCP returned failed:** `{fallback.get('error', '')}`")
                                job_status = None
                        except Exception as ex:
                            t_mcp = _time.time() - t_mcp_start
                            debug_lines.append(f"❌ **MCP error:** `{ex}` ({t_mcp:.2f}s)")

                    debug_lines.append(f"✅ **Final job_status:** `{job_status}`")
                    _dbg(f"TAB4 FINAL: {job_status}")

                    elapsed = _time.time() - st.session_state.multi_submit_time if st.session_state.multi_submit_time else 0
                    mins, secs = divmod(int(elapsed), 60)
                    _dbg(f"TAB4 elapsed: {mins}m{secs}s")

                    # Show debug panel
                    with st.expander(f"🐛 DEBUG — Poll #{poll_count} (remove after debugging)", expanded=True):
                        for line in debug_lines:
                            st.markdown(line)

                    if job_status == "Succeeded":
                        if not st.session_state.multi_status:
                            _dbg("TAB4 FETCHING FULL RESULT...")
                            # 1) Try direct Speech API
                            direct_result = fetch_multi_result_direct(job_url)
                            if direct_result:
                                st.session_state.multi_status = direct_result
                                _dbg(f"TAB4 RESULT VIA DIRECT API ✅")
                            else:
                                # 2) Fall back to MCP
                                _dbg("TAB4 DIRECT FAILED — trying MCP...")
                                try:
                                    mcp_result = call_mcp_tool("multi_transcription_status", {
                                        "job_url": job_url,
                                        "email": st.session_state.user_email
                                    })
                                    mcp_data = mcp_result.get("data") if isinstance(mcp_result, dict) else None
                                    if isinstance(mcp_data, dict) and mcp_data.get("status") == "Succeeded":
                                        st.session_state.multi_status = mcp_result
                                        _dbg(f"TAB4 RESULT VIA MCP ✅")
                                    else:
                                        _dbg(f"TAB4 MCP ALSO INCOMPLETE — retrying: {json.dumps(mcp_result,default=str)[:200]}")
                                        st.warning(f"⚠️ Both direct API and MCP returned incomplete data — retrying...")
                                        _time.sleep(5)
                                        st.rerun()
                                except Exception as e:
                                    _dbg(f"TAB4 MCP RESULT ERROR: {e}")
                                    st.error(f"Error fetching result: {e}")
                        st.session_state.multi_polling = False
                        _dbg("TAB4 POLLING STOPPED — Succeeded")
                        st.rerun()
                    elif job_status in ("Failed", "Cancelled"):
                        st.session_state.multi_polling = False
                        _dbg(f"TAB4 POLLING STOPPED — {job_status}")
                        st.error(f"❌ Job {job_status}")
                    elif poll_count >= 120:
                        st.session_state.multi_polling = False
                        _dbg("TAB4 POLLING STOPPED — 120 cycle limit")
                        st.error("❌ Polling stopped after 120 cycles (~10 min). Check debug info above.")
                    elif job_status is None:
                        _dbg(f"TAB4 STATUS UNKNOWN (None) — both direct & MCP failed, retrying...")
                        st.warning("⚠️ Could not determine job status (both direct API & MCP returned nothing). Retrying...")
                        st.markdown(f"**⏱ Elapsed:** {mins}m {secs}s")
                        _time.sleep(5)
                        st.rerun()
                    else:
                        _dbg(f"TAB4 CONTINUE — status={job_status} sleeping 5s")
                        st.markdown(f"**⏱ Elapsed:** {mins}m {secs}s")
                        for fi in files_info:
                            if fi["status"] == "uploaded":
                                st.progress(0.1, text=f"⏳ {fi['name']} — video processing")
                        _time.sleep(5)
                        st.rerun()

        elif result.get("status") == "unauthorized":
            st.error("🔒 " + result.get("error", "Access Denied"))
        else:
            st.error("❌ Transcription Failed: " + result.get("error", "Unknown error"))

    # ---- DISPLAY TRANSCRIPTION RESULTS ----
    if st.session_state.multi_status:
        status = st.session_state.multi_status

        if status.get("status") == "success":
            data = status.get("data") or {}
            job_status = data.get("status", "Unknown") if isinstance(data, dict) else "Unknown"

            if job_status == "Succeeded":
                file_results = data.get("files", [])
                total_text = data.get("total_text", "")
                completed = data.get("completed_count", 0)
                total_count = data.get("total_count", 0)

                # Total elapsed time
                elapsed = 0
                if st.session_state.multi_submit_time:
                    elapsed = _time.time() - st.session_state.multi_submit_time
                mins, secs = divmod(int(elapsed), 60)

                st.success(f"✅ Transcription Complete — **{completed}/{total_count}** files transcribed in **{mins}m {secs}s**")

                # Per-file results
                if file_results:
                    st.subheader("📊 Per-File Results")
                    for i, fr in enumerate(file_results):
                        icon = "✅" if fr["status"] == "completed" else "⚠️"
                        label = "video processing completed" if fr["status"] == "completed" else fr["status"]
                        st.progress(1.0, text=f"{icon} {fr['name']} — {label}")
                        with st.expander(f"{icon} {fr['name']} — {fr['status']}", expanded=(i == 0)):
                            if fr.get("text"):
                                st.text_area("", fr["text"], height=200, key=f"multi_file_{i}")
                            else:
                                st.info("No speech detected in this file")

                # Combined text
                if total_text:
                    st.subheader("📝 Combined Transcription")
                    st.text_area("", total_text, height=400, key="multi_combined_text")

                    # Downloads
                    st.download_button("📄 Download Text", total_text, "batch_transcription.txt", key="multi_dl_txt")

                    # Per-file JSON
                    json_output = json.dumps({
                        "total_files": total_count,
                        "completed_files": completed,
                        "files": file_results,
                        "combined_text": total_text
                    }, indent=2)

                    st.download_button(
                        "📥 Download JSON",
                        json_output,
                        "batch_transcription.json",
                        mime="application/json",
                        key="multi_dl_json"
                    )

            elif job_status in ("Failed", "Cancelled"):
                st.error(f"❌ Job {job_status}")
            else:
                st.info(f"📋 Job Status: **{job_status}**")

        elif status.get("status") == "unauthorized":
            st.error("🔒 " + status.get("error", "Access Denied"))
        else:
            st.error("❌ Status check failed: " + status.get("error", "Unknown error"))
