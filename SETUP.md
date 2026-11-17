# Set up CreateShorts

CreateShorts runs a Python video-processing backend and a React dashboard. Docker is the easiest way to install the Python, FFmpeg, and model dependencies together.

## What you need

- Git, Docker, and Docker Compose v2, with Docker running.
- Enough free disk space for container images, downloaded models, source videos, and exported clips. The first build and first transcription download models and take longer.
- Ollama for choosing moments, or another compatible AI endpoint.
- A video with spoken audio to try the clip generator.

CPU processing works; a GPU is optional. RAM needs depend on the AI model you choose, and larger models or context windows need more memory.

## 1. Get the project

```sh
git clone https://github.com/pavich5/CreateShorts.git
cd CreateShorts
cp .env.example .env
```

Edit `.env`. Leave unused services unset, and remove the example AWS credentials if you do not use S3.

## 2. Configure moment selection

Install Ollama using its [official download](https://ollama.com/download), then confirm `ollama --version` works. If Ollama is already running as an app or service, quit it before starting a server with these settings. In a macOS or Linux terminal, leave this command running:

```sh
OLLAMA_HOST=0.0.0.0:11434 OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

This makes the server reachable from Docker and sets the context needed for transcripts. Keep this unauthenticated service on your trusted local network. Windows users can set these environment variables through their account settings and restart Ollama; see the [official server configuration instructions](https://docs.ollama.com/faq#how-do-i-configure-ollama-server).

In a second terminal, download the example model:

```sh
ollama pull qwen2.5:14b
```

This example matches the project's environment template. You can substitute an instruction-following model that fits your hardware. Model downloading and starting are described in the [Ollama quickstart](https://docs.ollama.com/quickstart).

Add these settings to `.env`:

```dotenv
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:14b
LLM_TIMEOUT=600
# LLM_API_KEY=your-server-key-if-required
```

Docker Desktop provides `host.docker.internal`. On Linux, replace it with your host's reachable address or add `host.docker.internal:host-gateway` under the backend's `extra_hosts`. If you downloaded a different model, use its name in `LLM_MODEL`. Other OpenAI-compatible chat servers can use their own endpoint and model name.

You can also add `GEMINI_API_KEY` to `.env` for frame-based analysis, automatic layout selection, and silent-video analysis. Without it, the transcript-based path uses the configured chat server and visual layout analysis falls back to the standard crop.

## 3. Start the app

From the project root:

```sh
docker compose -f docker-compose.yml up --build -d
docker compose -f docker-compose.yml ps
```

Open [http://localhost:5175](http://localhost:5175). The backend is at [http://localhost:8000](http://localhost:8000), and its API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

Upload a video, choose the clip count and duration, and generate shorts. Open a result to adjust captions, hooks, framing, or images, then export it. Whisper transcribes speech locally; transcript selection uses your configured AI endpoint.

Generated projects and exports are stored under `output/` in the checkout. Keep that folder to retain your generated work. The default Compose setup opens the local dashboard without a `VITE_LOCAL_MODE` setting.

View logs or stop the app:

```sh
docker compose -f docker-compose.yml logs -f backend
docker compose -f docker-compose.yml down
```

After changing `.env`, restart the backend. Rebuild after changing dependencies or Dockerfiles:

```sh
docker compose -f docker-compose.yml restart backend
docker compose -f docker-compose.yml up --build -d
```

## Optional: a local video library

The folder library reads `/clips` inside the backend container. To use an existing folder on your computer, create `docker-compose.override.yml` beside the main Compose file:

```yaml
services:
  backend:
    volumes:
      - /absolute/path/to/your/video-library:/clips
```

Replace the example path with your real folder, then include the override when starting the app:

```sh
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

The app scans this folder and saves exported copies into category folders. Use a folder whose contents you want the local app to access.

## Optional: Codex-assisted editing

Codex-specific AI editing requires a separately installed compatible host adapter; this checkout does not include or start one. If you already have that adapter running, configure its actual port:

```dotenv
LLM_BASE_URL=http://host.docker.internal:8766/v1
LLM_MODEL=codex-account
CODEX_EDITOR_ENABLED=1
```

Signing into the Codex desktop app alone does not start this adapter. Keep authentication on the host; normal account limits apply.

## Frontend development

Use Node.js 22 and npm if you want to run the dashboard outside Docker. Keep the backend and renderer in Docker:

```sh
docker compose -f docker-compose.yml up --build -d backend renderer
cd dashboard
npm ci
VITE_PROXY_TARGET=http://localhost:8000 VITE_RENDER_TARGET=http://localhost:3100 npm run dev -- --host 127.0.0.1 --port 5175
```

If the Docker frontend is already using port 5175, stop that service first. Frontend checks:

```sh
npm run build
npm run test:unit
```

The backend uses Python 3.11, FFmpeg, and system libraries installed by the root `Dockerfile`; use Docker to keep these dependencies consistent.

## Publish your own copy to GitHub

The source repository is [pavich5/CreateShorts](https://github.com/pavich5/CreateShorts). To publish a separate copy under your own account, install GitHub CLI, sign in, and run these commands from your checkout. Replace `YOUR_USERNAME` with your GitHub username:

```sh
gh auth login
git status --short
gh repo create YOUR_USERNAME/CreateShorts --public --source=. --remote=my-github
git push -u my-github HEAD:main
```

Commit changes you want to include before pushing. Preserve [LICENSE](LICENSE), [NOTICE](NOTICE), and [cloud/LICENSE](cloud/LICENSE), including their upstream attribution and separate cloud terms. Never commit `.env`, private videos, local account data, or generated files. GitHub stores the source; running the backend requires a computer or server with Docker.

## Common problems

- **Analysis unavailable:** check `LLM_BASE_URL`, `LLM_MODEL`, and that the model server is reachable from the backend container. Reload the dashboard after restarting the backend.
- **Codex editor unavailable:** the compatible host adapter must be running; an ordinary chat server does not provide Codex-specific editing.
- **First run is slow:** allow model downloads to finish. CPU video processing can take time; check backend logs for progress.
- **Port already in use:** stop the existing service or change the published port in your local Compose configuration.
- **A YouTube URL fails:** try uploading a video file you own or have permission to process. Downloads may need fresh YouTube cookies.
