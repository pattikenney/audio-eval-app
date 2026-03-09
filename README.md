# Human Evaluation (Streamlit)

Streamlit app for human evaluation: play context audio, choose the best agent response, and record answers in a Google Sheet.

## Setup

1. **Secrets**  
   `.streamlit/secrets.toml` is already set up with your Google Sheet and service account credentials.

2. **Admin password** (optional)  
   To set a custom admin password, add to `.streamlit/secrets.toml`:
   ```toml
   admin_password = "your_secret_password"
   ```
   If omitted, the default is `admin`.

3. **Google Sheet**  
   - Share the sheet with the service account email (from `client_email` in secrets) with **Editor** access so the app can append response rows.
   - The app writes to a worksheet named `Sheet1`. It will create it and add headers (`user_name`, `timestamp`, `context_id`, `chosen_agent_id`) if missing.

4. **Config**  
   Edit `config.yaml` (or use the Admin view) to set:
   - `question_text`: prompt shown above the agent choices.
   - `eval_items`: list of `{ context_id: "<Drive file ID>", agent_ids: ["<id1>", "<id2>", ...] }`.  
   Use Google Drive file IDs for context and agent audio files.

5. **Drive files**  
   For audio to be playable by anyone, share each file (or a parent folder) so “Anyone with the link” can view, or ensure the app’s service account has access if you use other sharing.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Features

- **User view**: Progress bar, context audio player, randomized agent options (radio), Submit (saves to sheet and advances), thank-you screen when done.
- **Admin view**: Password-protected; edit `question_text` and eval items (context_id and agent_ids) and save to `config.yaml`.
