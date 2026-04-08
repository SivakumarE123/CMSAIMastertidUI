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

load_dotenv()

MCP_URL = os.getenv("MCP_URL", "")

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
    async def _call():
        async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                if result.isError:
                    error_msg = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            error_msg += content.text
                    raise Exception(f"MCP tool error: {error_msg}")

                for content in result.content:
                    if hasattr(content, "text"):
                        return json.loads(content.text)

                return {}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_call())
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
    if not _SPEECH_KEY or not job_url:
        return None
    try:
        resp = _speech_session.get(job_url, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("status", "Unknown")
        else:
            print(f"[WARN] Speech API status check returned {resp.status_code}")
    except Exception as e:
        print(f"[WARN] Speech API direct check failed: {e}")
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

if "multi_submit_time" not in st.session_state:
    st.session_state.multi_submit_time = None

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "user_email" not in st.session_state:
    st.session_state.user_email = None

if "user_name" not in st.session_state:
    st.session_state.user_name = None


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
                    # Fast direct API check first
                    job_status = check_speech_job_status(job_url)
                    print(f"[POLL-VIDEO] direct check → {job_status}")

                    # Fallback to MCP if direct check unavailable
                    if job_status is None:
                        try:
                            print("[POLL-VIDEO] direct unavailable, falling back to MCP...")
                            fallback = call_mcp_tool("transcription_status", {
                                "job_url": job_url,
                                "email": st.session_state.user_email
                            })
                            if isinstance(fallback, dict) and fallback.get("status") == "success":
                                inner = fallback.get("data") or {}
                                if isinstance(inner, dict):
                                    job_status = inner.get("status", "Running")
                                    if job_status == "Succeeded":
                                        st.session_state.transcription_status = fallback
                            print(f"[POLL-VIDEO] MCP fallback → {job_status}")
                        except Exception as ex:
                            print(f"[POLL-VIDEO] MCP fallback error: {ex}")

                    elapsed = _time.time() - st.session_state.video_submit_time if st.session_state.video_submit_time else 0
                    mins, secs = divmod(int(elapsed), 60)

                    if job_status == "Succeeded":
                        # Fetch full result via MCP (one call)
                        if not st.session_state.transcription_status:
                            try:
                                st.session_state.transcription_status = call_mcp_tool("transcription_status", {
                                    "job_url": job_url,
                                    "email": st.session_state.user_email
                                })
                            except Exception as e:
                                st.error(f"Error fetching result: {e}")
                        st.session_state.video_polling = False
                        st.rerun()
                    elif job_status in ("Failed", "Cancelled"):
                        st.session_state.video_polling = False
                        st.error(f"❌ Job {job_status}")
                    else:
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
                    # Fast direct API check first
                    job_status = check_speech_job_status(job_url)
                    print(f"[POLL-MULTI] direct check → {job_status}")

                    # Fallback to MCP if direct check unavailable
                    if job_status is None:
                        try:
                            print("[POLL-MULTI] direct unavailable, falling back to MCP...")
                            fallback = call_mcp_tool("multi_transcription_status", {
                                "job_url": job_url,
                                "email": st.session_state.user_email
                            })
                            if isinstance(fallback, dict) and fallback.get("status") == "success":
                                inner = fallback.get("data") or {}
                                if isinstance(inner, dict):
                                    job_status = inner.get("status", "Running")
                                    if job_status == "Succeeded":
                                        st.session_state.multi_status = fallback
                            print(f"[POLL-MULTI] MCP fallback → {job_status}")
                        except Exception as ex:
                            print(f"[POLL-MULTI] MCP fallback error: {ex}")

                    elapsed = _time.time() - st.session_state.multi_submit_time if st.session_state.multi_submit_time else 0
                    mins, secs = divmod(int(elapsed), 60)

                    if job_status == "Succeeded":
                        if not st.session_state.multi_status:
                            try:
                                st.session_state.multi_status = call_mcp_tool("multi_transcription_status", {
                                    "job_url": job_url,
                                    "email": st.session_state.user_email
                                })
                            except Exception as e:
                                st.error(f"Error fetching result: {e}")
                        st.session_state.multi_polling = False
                        st.rerun()
                    elif job_status in ("Failed", "Cancelled"):
                        st.session_state.multi_polling = False
                        st.error(f"❌ Job {job_status}")
                    else:
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
