"""
Human evaluation Streamlit app.
User view: progress, context audio, randomized agent choices, submit.
Admin view: password-protected edit of question_text and eval_items in config.yaml.
"""

import re
import random
from datetime import datetime
from pathlib import Path

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

from streamlit_gsheets import GSheetsConnection
from utils.helpers import (
    load_config,
    save_config,
    save_config_as,
    get_local_audio_path,
    get_active_config_filename,
    get_active_config_path,
    set_active_config,
    list_config_files,
    convert_config_to_relative_paths,
    CONFIG_PATH,
)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
RESPONSES_SHEET_NAME = "Sheet1"  # worksheet for saving responses


def get_spreadsheet_id() -> str:
    url = st.secrets["connections"]["gsheets"]["spreadsheet"]
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else ""


def get_gsheets_conn():
    """Return GSheets connection for reading."""
    return st.connection("gsheets", type=GSheetsConnection)


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client():
    """Return gspread client for appending rows (write)."""
    raw = dict(st.secrets["connections"]["gsheets"])
    creds_dict = {k: v for k, v in raw.items() if k != "spreadsheet"}
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def append_response_to_sheet(
    user_name: str,
    context_path: str,
    chosen_agent_path: str,
    config_name: str = "",
) -> None:
    """Append one response row to the Google Sheet."""
    client = get_gspread_client()
    spreadsheet = client.open_by_key(get_spreadsheet_id())
    try:
        worksheet = spreadsheet.worksheet(RESPONSES_SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(RESPONSES_SHEET_NAME, rows=1000, cols=10)
        worksheet.append_row(
            ["user_name", "timestamp", "config_name", "context_path", "chosen_agent_path"]
        )
    row = [
        user_name,
        datetime.utcnow().isoformat() + "Z",
        config_name,
        context_path,
        chosen_agent_path,
    ]
    worksheet.append_row(row)


def init_session_state(eval_items: list):
    """Initialize session state for user view. Randomize question order once per session."""
    if "current_index" not in st.session_state:
        st.session_state.current_index = 0
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "shuffled_agents_current" not in st.session_state:
        st.session_state.shuffled_agents_current = None
    # Randomize question order once when user first joins (one order for the whole session)
    if "question_order" not in st.session_state:
        n = len(eval_items)
        st.session_state.question_order = random.sample(range(n), n)


def render_user_view(config: dict):
    """Render the main evaluation UI: progress, context audio, agent choices, submit."""
    eval_items = config.get("eval_items") or []
    question_text = config.get("question_text") or "Which response sounds most appropriate to the context?"

    if not eval_items:
        st.info("No evaluation items configured. Add items in the Admin view.")
        return

    init_session_state(eval_items)
    idx = st.session_state.current_index
    question_order = st.session_state.question_order

    if idx >= len(eval_items):
        st.balloons()
        st.success("Thank you! You have completed all questions.")
        return

    # Show question at randomized position
    item_index = question_order[idx]
    item = eval_items[item_index]
    context_path = (item.get("context_path") or "").strip()
    agent_paths = [p for p in (item.get("agent_paths") or []) if (p or "").strip()]

    if not context_path or not agent_paths:
        st.warning(
            f"Question {idx + 1} of {len(eval_items)} has missing context_path or agent_paths. Skip or fix in Admin."
        )
        if st.button("Skip this item"):
            st.session_state.current_index += 1
            st.rerun()
        return

    # Progress bar
    progress = (idx + 1) / len(eval_items)
    st.progress(progress, text=f"Question {idx + 1} of {len(eval_items)}")

    # User name (once)
    if not st.session_state.user_name:
        name = st.text_input("Enter your email", key="user_name_input")
        st.caption("Always use your email written exactly the same, caps sensitive.")
        if name:
            st.session_state.user_name = name.strip()
            st.rerun()
        return

    # Context audio (local file)
    st.subheader("Context")
    context_file = get_local_audio_path(context_path)
    if context_file.exists():
        st.audio(str(context_file))
    else:
        st.warning(f"Audio file not found: {context_path}")

    # Shuffle agent order once per question (so every user sees random order)
    if st.session_state.shuffled_agents_current != idx:
        st.session_state.shuffled_agents_current = idx
        st.session_state.shuffled_agent_paths = agent_paths.copy()
        random.shuffle(st.session_state.shuffled_agent_paths)

    shuffled = st.session_state.shuffled_agent_paths
    n_agents = len(shuffled)
    labels = [f"Agent {chr(65 + i)}" for i in range(n_agents)]

    # Per-question selection state
    choice_key = f"choice_{idx}"
    if choice_key not in st.session_state:
        st.session_state[choice_key] = None

    # Each agent: audio player first, then selection control directly underneath (4-way: Agent A, B, C, D)
    st.subheader(question_text)
    cols = st.columns(n_agents)
    for i, col in enumerate(cols):
        with col:
            agent_file = get_local_audio_path(shuffled[i])
            if agent_file.exists():
                st.audio(str(agent_file))
            else:
                st.caption(f"File not found: {shuffled[i]}")
            label = labels[i]
            if st.button(f"Select {label}", key=f"sel_{idx}_{i}"):
                st.session_state[choice_key] = (label, shuffled[i])
                st.rerun()
            if st.session_state[choice_key] and st.session_state[choice_key][0] == label:
                st.caption("✓ Selected")

    chosen_agent_path = None
    if st.session_state[choice_key]:
        _, chosen_agent_path = st.session_state[choice_key]

    if not chosen_agent_path:
        st.caption("Select an option above, then click Submit.")

    config_name = config.get("config_name") or ""

    if st.button("Submit", disabled=(chosen_agent_path is None)):
        if chosen_agent_path:
            append_response_to_sheet(
                st.session_state.user_name,
                context_path,
                chosen_agent_path,
                config_name=config_name,
            )
            st.session_state.current_index += 1
            st.rerun()


def render_admin_view():
    """Password-protected admin: edit question_text and eval_items, save to config.yaml."""
    st.subheader("Admin")

    if "admin_unlocked" not in st.session_state:
        st.session_state.admin_unlocked = False

    if not st.session_state.admin_unlocked:
        pwd = st.text_input("Admin password", type="password", key="admin_pwd")
        if st.button("Unlock"):
            # Simple check: store expected password in secrets or env for real use
            expected = st.secrets.get("admin_password", "admin")
            if pwd == expected:
                st.session_state.admin_unlocked = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return

    if st.button("Lock admin"):
        st.session_state.admin_unlocked = False
        st.rerun()

    if "admin_extra_items" not in st.session_state:
        st.session_state.admin_extra_items = []
    if "admin_editing_blank" not in st.session_state:
        st.session_state.admin_editing_blank = False

    # Load config for the form: either blank (new) or from file
    if st.session_state.admin_editing_blank:
        config = {
            "config_name": "",
            "question_text": "Which response sounds most appropriate to the context?",
            "eval_items": [],
        }
    else:
        try:
            config = load_config()
        except Exception as e:
            st.error(f"Could not load config: {e}")
            return

    # ---------- Config Manager ----------
    st.subheader("Config Manager")
    active_name = get_active_config_filename()
    if st.session_state.admin_editing_blank:
        st.caption("Editing **new blank config**. Save as new config when ready.")
    elif active_name:
        st.caption(f"Active config: **{active_name}** (used by the app and User view)")
    else:
        st.caption("Active config: *root config.yaml* (save a config below to use configs/)")

    if st.button("Start new config"):
        st.session_state.admin_editing_blank = True
        st.session_state.admin_extra_items = []
        st.success("Form cleared. Fill in your config and use 'Save as new config' at the bottom.")
        st.rerun()

    config_files = list_config_files()
    if config_files:
        selected = st.selectbox(
            "Load a config (set as active so the app uses it)",
            options=config_files,
            index=(config_files.index(active_name) if active_name and active_name in config_files else 0),
            key="admin_config_select",
        )
        if st.button("Set as active config"):
            set_active_config(selected)
            st.session_state.admin_editing_blank = False
            st.success(f"Active config is now **{selected}**.")
            st.rerun()
    else:
        st.caption("No config files in configs/ yet. Use 'Save as new config' at the bottom.")

    st.divider()
    st.subheader("Edit current config")

    config_name = st.text_input(
        "Config name (version / identifier for this config)",
        value=config.get("config_name") or "",
        key="admin_config_name",
    )
    question_text = st.text_area(
        "Question text",
        value=config.get("question_text") or "",
        key="admin_question",
    )

    eval_items = (config.get("eval_items") or []) + st.session_state.admin_extra_items
    if not eval_items:
        eval_items = [{"context_path": "", "agent_paths": []}]
    st.write(
        "Evaluation items. You can use **absolute paths** (e.g. `/Users/you/Downloads/file.m4a`) "
        "or relative (e.g. `audio/file.m4a`). Use **Convert to relative paths** below before pushing to Streamlit."
    )

    new_items = []
    for i, item in enumerate(eval_items):
        with st.expander(f"Item {i + 1}", expanded=(i == 0)):
            ctx = st.text_input(
                "Context path (absolute or audio/...)",
                value=item.get("context_path") or "",
                key=f"ctx_{i}",
                placeholder="/Users/you/Downloads/context.m4a or audio/context.m4a",
            )
            agents_str = st.text_input(
                "Agent paths (comma-separated; absolute or audio/...)",
                value=",".join(item.get("agent_paths") or []),
                key=f"agents_{i}",
                placeholder="/path/to/agent1.mp3, audio/agent2.mp3",
            )
            agent_paths = [x.strip() for x in agents_str.split(",") if x.strip()]
            new_item = {"context_path": ctx, "agent_paths": agent_paths}
            if item.get("id") is not None:
                new_item["id"] = item["id"]
            new_items.append(new_item)

    if st.button("Add another item"):
        st.session_state.admin_extra_items.append({"context_path": "", "agent_paths": []})
        st.rerun()

    if st.button("Save config"):
        if st.session_state.admin_editing_blank:
            st.warning("This is a new blank config. Use 'Save as new config' at the bottom to create a file first.")
        else:
            config["config_name"] = config_name
            config["question_text"] = question_text
            config["eval_items"] = new_items
            save_config(config)
            st.session_state.admin_extra_items = []
            where = get_active_config_path() or CONFIG_PATH
            st.success(f"Config saved to **{where.name}**.")
            st.rerun()

    st.divider()
    st.subheader("Deployment: Convert to relative paths")
    st.caption(
        "If you used absolute paths above, click this to copy those files into **audio/** "
        "and rewrite the config to use **audio/filename**. Then save; the config will be ready to push to Streamlit."
    )
    if st.button("Convert to relative paths (copy files to audio/ and update config)"):
        cfg = {
            "config_name": config_name,
            "question_text": question_text,
            "eval_items": new_items,
        }
        updated_cfg, messages = convert_config_to_relative_paths(cfg)
        if not messages:
            st.info("No absolute paths found; config already uses relative paths.")
        else:
            for msg in messages:
                st.caption(msg)
            config.clear()
            config.update(updated_cfg)
            save_config(updated_cfg)
            st.session_state.admin_extra_items = []
            st.success(
                "Files copied to **audio/** and config updated with relative paths. "
                "Save or Save as new config if needed, then push to GitHub/Streamlit."
            )
            st.rerun()

    st.divider()
    st.subheader("Save as new config")
    save_as_name = st.text_input(
        "Filename for new config (config_name will be set to this name)",
        placeholder="e.g. phase1_march9",
        key="admin_save_as_name",
    )
    if st.button("Save as new config"):
        if not (save_as_name or "").strip():
            st.error("Enter a filename (e.g. phase1_march9).")
        else:
            try:
                cfg = {
                    "config_name": Path(save_as_name.strip()).stem,
                    "question_text": question_text,
                    "eval_items": new_items,
                }
                path = save_config_as(cfg, save_as_name.strip())
                set_active_config(path.name)
                st.session_state.admin_extra_items = []
                st.session_state.admin_editing_blank = False
                st.success(f"Saved as **{path.name}** and set as active. config_name = '{path.stem}' (tracked in Sheet).")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    st.caption(f"Active config file: {get_active_config_path() or CONFIG_PATH}")


def main():
    st.set_page_config(page_title="Phase 1 Human Evaluation- Contextually Appropriate Voice Agent", layout="wide")
    st.title("Phase 1 Human Evaluation- Contextually Appropriate Voice Agent")

    try:
        config = load_config()
    except FileNotFoundError:
        st.error("No config found. In Admin, save a config (Config Manager → Save as new config) or add config.yaml.")
        return
    except Exception as e:
        st.error(f"Error loading config: {e}")
        return

    mode = st.sidebar.radio("Mode", ["User (evaluate)", "Admin"], key="mode")

    if mode == "Admin":
        render_admin_view()
    else:
        render_user_view(config)


if __name__ == "__main__":
    main()
