# SOP_Chatbot — Local RAG Chatbot for SOP / Policy Documents

A fully local, retrieval-augmented chatbot that answers questions directly from Standard Operating Procedure (SOP) documents. No external APIs — 100% self-hosted inference, so no proprietary/sensitive data ever leaves the network.

> **Note:** This is a portfolio version of a production internal tool built for a healthcare billing operations team. Real client SOP data, internal server addresses, and credentials have been removed/redacted. Sample data structure only.

## Stack

| Component | Technology |
|---|---|
| UI | Streamlit (custom themed, streaming chat, role-based login) |
| LLM | Ollama — `qwen2.5:14b` (swappable to `7b`/`32b` depending on GPU) |
| Retrieval | Custom keyword-relevance scoring filter (see "Engine Design" below) |
| SOP data | JSON schemas converted from Excel / CSV / DOCX source documents |
| Deployment | Docker Compose + Nginx (SSL termination) / Kubernetes (production) |

## Architecture

```
Browser → Nginx (SSL) → Docker container (Streamlit app)
                              ↓
                   hybrid_sop_engine.py
                   Generic relevance filter (no hardcoded rules)
                   → narrows records by query word score
                              ↓
                   Ollama container (internal only)
                   qwen2.5:14b on GPU, streaming response
```

**Engine design:** A generic scoring filter (`smart_filter`) narrows hundreds of SOP records down to the ~5-30 most relevant before they ever reach the LLM — no hardcoded keyword lists or category rules, so it generalizes to any SOP document set automatically. The LLM then reads only the filtered records as plain text and answers using full language understanding, rather than brittle exact-match retrieval.

**Zero-downtime content reload:** `sop_watcher.py` (cron-based) detects new files in a drop-folder, converts them to the JSON schema, and writes a flag file. The app picks up the flag on next page load and clears its cached engine — no container restart needed to add new SOP content.

## Project Structure

```
SOPhia/
├── streamlit_gemini.py          # Main Streamlit app (auth, chat, theming, streaming)
├── hybrid_sop_engine.py         # Retrieval engine: relevance filter + Ollama client
├── sop_watcher.py               # Drop-folder watcher (run by cron)
├── setup_watcher.sh             # One-time server setup for drop-folder automation
├── convert_everhealth.py        # ETL: source xlsx/csv/docx → JSON schema
├── convert_other_vertical.py    # ETL: alternate source format → JSON schema
├── data_schema/                 # Converted JSON records (excluded here — see below)
├── nginx/nginx.conf             # Docker nginx config (SSL termination example)
├── docker-compose.yml           # Full Docker stack (Ollama GPU + App)
├── Dockerfile                   # python:3.11 image, hot-reload disabled for prod
├── requirements.txt             # Python dependencies
└── .env.example                 # Config template (copy to .env, fill in real values)
```

> **`data_schema/` and `data/` are intentionally empty/excluded in this public repo** — they contained real, confidential client SOP content in the original deployment. To run this yourself, add your own SOP files under `data/` and run the converter scripts to generate `data_schema/*.json`.

## Local Development

**Prerequisites:** Python 3.11+, Ollama installed and running locally.

```bash
# 1. Clone
git clone https://github.com/chandrashekar-y/SOP-Chatbot.git
cd SOP-Chatbot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — set OLLAMA_BASE_URL=http://localhost:11434 for local dev

# 4. Pull the model
ollama pull qwen2.5:14b
ollama pull nomic-embed-text

# 5. Add your own SOP source files under data/, then generate schemas
python convert_everhealth.py
python convert_other_vertical.py

# 6. Run
streamlit run streamlit_gemini.py
```

App available at `http://localhost:8501`.

## Deployment

This project supports two deployment modes:
- **Docker Compose** — single-server setup, Ollama + app + nginx as separate containers, GPU passthrough via `nvidia-container-toolkit`
- **Kubernetes** — production-grade, CI/CD-triggered rebuilds on git push, manager/ops-owned redeploy

See `docker-compose.yml` and `Dockerfile` for the containerization approach. In production, `data_schema/` can either be baked into the image (rebuild required per content update) or bind-mounted/hostPath-volume-mounted for live updates without rebuilding.

## GPU & Ollama Settings (reference)

| Setting | Value | Purpose |
|---|---|---|
| Model | `qwen2.5:14b` | Main LLM |
| `OLLAMA_KEEP_ALIVE` | `-1` | Model stays resident in VRAM |
| `OLLAMA_NUM_PARALLEL` | `2` | Concurrent request cap (tune to VRAM budget) |
| `OLLAMA_FLASH_ATTENTION` | `1` | Faster attention kernel |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | One model resident at a time |
| `num_ctx` | `16384` | Context window |
| `num_predict` | `768` | Max answer tokens |
| `temperature` | `0.1` | Deterministic, factual answers |

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `OLLAMA_LLM_MODEL` | Model name | `qwen2.5:14b` |
| `SCHEMA_DIR` | Path to JSON schema files | `./data_schema` |
| `EVERHEALTH_SOP_DIR` | Source files directory (format A) | `./data/Everhealth SOPs` |
| `OTHER_VERTICAL_SOP_DIR` | Source files directory (format B) | `./data/Other Vertical SOPs` |
| `FEEDBACK_FILE` | Feedback log path | `./feedback.jsonl` |

## Known Issues / Watch List

- Health check on the Ollama container may show "unhealthy" if it pings an external endpoint blocked on an internal network — this doesn't affect actual model serving; verify with `curl http://localhost:11434/api/ps`
- If answers are slow (>10s), check `nvidia-smi` — the model may have been pushed to CPU by a competing process
- If answers get cut off, increase `num_predict` in `hybrid_sop_engine.py`
- CSS/JS injection must use Streamlit's `st.html()`, not `st.markdown()` — newer Streamlit versions strip `<style>`/`<script>` tags from markdown for security

## Roadmap Ideas

- Hybrid retrieval: combine the current keyword-relevance filter with embedding-based semantic search (`nomic-embed-text` via Ollama) for better recall on paraphrased questions
- Cross-encoder reranking pass before final context assembly
- RAGAS-based evaluation harness with a golden Q&A test set, wired into CI
- Source citation display in the UI (data already tracked internally, just needs surfacing)
