"""
Human evaluation Streamlit app.
User view: progress, context audio, randomized agent choices, submit.
Admin view: password-protected edit of question_text and eval_items in config.yaml.
"""

import re
import random
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

from streamlit_gsheets import GSheetsConnection
import yaml
from utils.helpers import (
    load_config,
    save_config,
    save_config_as,
    get_local_audio_path,
    get_asset_path,
    get_active_config_filename,
    get_active_config_path,
    set_active_config,
    list_config_files,
    convert_config_to_relative_paths,
    CONFIG_PATH,
    CONFIGS_DIR,
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
    activation_score: Optional[Union[str, int]] = None,
    dominance_score: Optional[Union[str, int]] = None,
) -> None:
    """Append one response row to the Google Sheet."""
    client = get_gspread_client()
    spreadsheet = client.open_by_key(get_spreadsheet_id())
    try:
        worksheet = spreadsheet.worksheet(RESPONSES_SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(RESPONSES_SHEET_NAME, rows=1000, cols=12)
        worksheet.append_row(
            [
                "user_name",
                "timestamp",
                "config_name",
                "context_path",
                "chosen_agent_path",
                "activation_score (A1-A7)",
                "dominance_score (D1-D7)",
            ]
        )
    # Format scale values: A1–A7 for activation, D1–D7 for dominance
    act_cell = ""
    if activation_score is not None and activation_score in range(1, 8):
        act_cell = f"A{activation_score}"
    dom_cell = ""
    if dominance_score is not None and dominance_score in range(1, 8):
        dom_cell = f"D{dominance_score}"
    row = [
        user_name,
        datetime.utcnow().isoformat() + "Z",
        config_name,
        context_path,
        chosen_agent_path,
        act_cell,
        dom_cell,
    ]
    worksheet.append_row(row)


def init_session_state(eval_items: list):
    """Initialize session state for user view. Two-phase randomization: Group A (agent_comparison or missing type) then Group B (sam_rating, sam_full_emotional_rating), each group shuffled independently."""
    if "current_index" not in st.session_state:
        st.session_state.current_index = 0
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "shuffled_agents_current" not in st.session_state:
        st.session_state.shuffled_agents_current = None
    if "randomized_items" not in st.session_state:
        # Group A: type is agent_comparison OR type key is missing (default to comparison)
        group_a = [
            i for i in range(len(eval_items))
            if eval_items[i].get("type", "agent_comparison") == "agent_comparison"
        ]
        # Group B: type is sam_rating or sam_full_emotional_rating
        group_b = [
            i for i in range(len(eval_items))
            if eval_items[i].get("type") in ("sam_rating", "sam_full_emotional_rating")
        ]
        random.shuffle(group_a)
        random.shuffle(group_b)
        st.session_state.randomized_items = group_a + group_b


def render_user_view(config: dict):
    """Render the main evaluation UI: progress, context audio, agent choices, submit."""
    eval_items = config.get("eval_items") or []
    question_text = config.get("question_text") or "Which response sounds most appropriate to the context?"

    if not eval_items:
        st.info("No evaluation items configured. Add items in the Admin view.")
        return

    init_session_state(eval_items)
    idx = st.session_state.current_index
    question_order = st.session_state.randomized_items

    if idx >= len(eval_items):
        st.balloons()
        st.success("Thank you! You have completed all questions.")
        return

    # Show question at randomized position
    item_index = question_order[idx]
    item = eval_items[item_index]
    item_type = item.get("type") or "agent_comparison"
    context_path = (item.get("context_path") or "").strip()
    agent_paths = [p for p in (item.get("agent_paths") or []) if (p or "").strip()]

    if not context_path:
        st.warning(f"Question {idx + 1} has missing context_path. Skip or fix in Admin.")
        if st.button("Skip this item"):
            st.session_state.current_index += 1
            st.rerun()
        return
    if item_type == "agent_comparison" and not agent_paths:
        st.warning(f"Question {idx + 1} has missing agent_paths. Skip or fix in Admin.")
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

    # Instruction text by question type (default type to agent_comparison when missing)
    if item_type == "agent_comparison":
        instruction_text = "Listen to the context, then choose the agent response that sounds most appropriate."
    elif item_type == "sam_full_emotional_rating":
        instruction_text = "Listen to the audio clip, then rate the speaker's level of Activation and Dominance using the scales below."
    else:
        instruction_text = "Listen to the audio clip, then rate the speaker's level of Activation using the scale below."
    st.subheader(instruction_text)

    chosen_agent_path = None
    if item_type == "agent_comparison":
        # Shuffle agent order once per question
        if st.session_state.shuffled_agents_current != idx:
            st.session_state.shuffled_agents_current = idx
            st.session_state.shuffled_agent_paths = agent_paths.copy()
            random.shuffle(st.session_state.shuffled_agent_paths)
        shuffled = st.session_state.shuffled_agent_paths
        n_agents = len(shuffled)
        labels = [f"Agent {chr(65 + i)}" for i in range(n_agents)]
        choice_key = f"choice_{idx}"
        if choice_key not in st.session_state:
            st.session_state[choice_key] = None
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
        if st.session_state[choice_key]:
            _, chosen_agent_path = st.session_state[choice_key]

    if item_type in ("sam_rating", "sam_full_emotional_rating"):
        # Activation: one radio per column, synced via current_activation_score
        if "current_activation_score" not in st.session_state:
            st.session_state.current_activation_score = None
        st.subheader("Activation")
        st.markdown(
            """
            <style>
            /* Sledgehammer: force small font and no overlap on SAM labels */
            div[data-testid="stRadio"] label {
                font-size: 0.6rem !important;
                font-weight: 700 !important;
                white-space: nowrap !important;
                overflow: visible !important;
                text-align: center !important;
                margin-left: -5px !important;
                margin-right: -5px !important;
                line-height: 1.0 !important;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
            }
            /* Tighten columns: zero gap on horizontal block */
            div[data-testid="stHorizontalBlock"] {
                gap: 0px !important;
            }
            /* Kill Streamlit's 1rem column padding */
            div[data-testid="column"] {
                padding: 0px !important;
            }
            /* SAM 7-column grid: center image and radio */
            div[data-testid="column"]:has(img) {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: flex-start;
            }
            div[data-testid="column"]:has(img) > div:first-child {
                min-height: 100px;
                display: flex;
                align-items: flex-end;
                justify-content: center;
            }
            div[data-testid="column"]:has(img) img {
                max-height: 80px;
                width: auto;
                height: auto;
                object-fit: contain;
                display: block;
                margin-left: auto;
                margin-right: auto;
            }
            div[data-testid="column"]:has(img) div[data-testid="stRadio"] {
                width: 100%;
                display: flex;
                flex-direction: column;
                align-items: center;
                padding-left: 0 !important;
                padding-right: 0 !important;
                margin-top: 0 !important;
                padding-top: 0 !important;
            }
            div[data-testid="stRadio"] > div {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                text-align: center;
            }
            /* Hide radio group label and placeholder (ghost) row */
            div[data-testid="stRadio"] > label {
                display: none !important;
            }
            div[data-testid="stRadio"] [role="radiogroup"] > label:first-of-type {
                display: none !important;
            }
            div[data-testid="column"]:has(img) div[data-testid="stRadio"] > div > label:first-of-type {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        activation_labels = [
            "1 (Very calm)",
            "2 (calm)",
            "3 (somewhat calm)",
            "4 (neutral)",
            "5 (somewhat active)",
            "6 (active)",
            "7 (Very active)",
        ]
        act_cols = st.columns(7)
        for i, col in enumerate(act_cols):
            with col:
                img_path = get_asset_path(f"activation_{i + 1}.png")
                if img_path.exists():
                    st.image(str(img_path), use_container_width=True)
                # One radio per column; index=None when unselected to avoid defaulting to ghost
                act_options = [" ", activation_labels[i]]
                act_sel = 1 if st.session_state.current_activation_score == i + 1 else None
                act_choice = st.radio(
                    " ",
                    options=act_options,
                    index=act_sel,
                    key=f"act_{idx}_{i}",
                    label_visibility="collapsed",
                )
                if act_choice == activation_labels[i]:
                    st.session_state.current_activation_score = i + 1

    if item_type == "sam_full_emotional_rating":
        # Dominance: one radio per column, synced via current_dominance_score
        if "current_dominance_score" not in st.session_state:
            st.session_state.current_dominance_score = None
        st.subheader("Dominance")
        dominance_labels = [
            "1 (Very weak)",
            "2 (weak)",
            "3 (somewhat weak)",
            "4 (neutral)",
            "5 (somewhat strong)",
            "6 (strong)",
            "7 (Very strong)",
        ]
        dom_cols = st.columns(7)
        for i, col in enumerate(dom_cols):
            with col:
                img_path = get_asset_path(f"dominance_{i + 1}.png")
                if img_path.exists():
                    st.image(str(img_path), use_container_width=True)
                dom_options = [" ", dominance_labels[i]]
                dom_sel = 1 if st.session_state.current_dominance_score == i + 1 else None
                dom_choice = st.radio(
                    " ",
                    options=dom_options,
                    index=dom_sel,
                    key=f"dom_{idx}_{i}",
                    label_visibility="collapsed",
                )
                if dom_choice == dominance_labels[i]:
                    st.session_state.current_dominance_score = i + 1

    can_submit = (
        (item_type == "sam_rating" and st.session_state.get("current_activation_score") in range(1, 8))
        or (
            item_type == "sam_full_emotional_rating"
            and st.session_state.get("current_activation_score") in range(1, 8)
            and st.session_state.get("current_dominance_score") in range(1, 8)
        )
        or (item_type == "agent_comparison" and chosen_agent_path is not None)
    )
    if item_type == "agent_comparison" and not chosen_agent_path:
        st.caption("Select an option above, then click Submit.")

    config_name = config.get("config_name") or ""

    if st.button("Submit", disabled=not can_submit):
        # Capture SAM scores before reset (used for sheet)
        act_val = (
            st.session_state.get("current_activation_score")
            if item_type in ("sam_rating", "sam_full_emotional_rating")
            else None
        )
        dom_val = (
            st.session_state.get("current_dominance_score")
            if item_type == "sam_full_emotional_rating"
            else None
        )
        append_response_to_sheet(
            st.session_state.user_name,
            context_path,
            chosen_agent_path or "",
            config_name=config_name,
            activation_score=act_val,
            dominance_score=dom_val,
        )
        st.session_state.current_index += 1
        # Reset SAM state for next question so radios don't persist
        st.session_state.current_activation_score = None
        st.session_state.current_dominance_score = None
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
            item_type = item.get("type") or "agent_comparison"
            _type_options = ["agent_comparison", "sam_rating", "sam_full_emotional_rating"]
            _type_labels = {"agent_comparison": "Agent comparison", "sam_rating": "SAM rating", "sam_full_emotional_rating": "SAM full emotional rating"}
            qtype = st.selectbox(
                "Question type",
                options=_type_options,
                format_func=lambda x: _type_labels.get(x, x),
                index=_type_options.index(item_type) if item_type in _type_options else 0,
                key=f"type_{i}",
            )
            ctx = st.text_input(
                "Context path (absolute or audio/...)",
                value=item.get("context_path") or "",
                key=f"ctx_{i}",
                placeholder="/Users/you/Downloads/context.m4a or audio/context.m4a",
            )
            agents_str = st.text_input(
                "Agent paths (comma-separated; absolute or audio/...). Leave empty for SAM rating.",
                value=",".join(item.get("agent_paths") or []),
                key=f"agents_{i}",
                placeholder="/path/to/agent1.mp3, audio/agent2.mp3",
            )
            agent_paths = [x.strip() for x in agents_str.split(",") if x.strip()]
            new_item = {"context_path": ctx, "agent_paths": agent_paths, "type": qtype}
            if item.get("id") is not None:
                new_item["id"] = item["id"]
            new_items.append(new_item)

    if st.button("Add another item"):
        st.session_state.admin_extra_items.append({"context_path": "", "agent_paths": [], "type": "agent_comparison"})
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


def _load_target_config():
    """Load phase3final.yaml from configs/ or project root; ignores .active."""
    target_config = "phase3final.yaml"
    project_root = CONFIG_PATH.parent
    path_in_configs = CONFIGS_DIR / target_config
    path_in_root = project_root / target_config
    if path_in_configs.exists():
        with open(path_in_configs, "r") as f:
            return yaml.safe_load(f)
    if path_in_root.exists():
        with open(path_in_root, "r") as f:
            return yaml.safe_load(f)
    raise FileNotFoundError(
        "Error: phase3final.yaml not found. Please ensure the file is in the root directory or the configs/ folder."
    )


def main():
    st.set_page_config(page_title="Phase 3 Human Evaluation- Contextually Appropriate Voice Agent", layout="wide")
    st.title("Phase 3 Human Evaluation- Contextually Appropriate Voice Agent")

    try:
        config = _load_target_config()
    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Error loading config: {e}")
        return

    # Debug: verify item count
    eval_items = config.get("eval_items") or []
    st.sidebar.write(f"Loaded {len(eval_items)} items")

    mode = st.sidebar.radio("Mode", ["User (evaluate)", "Admin"], key="mode")

    if mode == "Admin":
        render_admin_view()
    else:
        render_user_view(config)


if __name__ == "__main__":
    main()
