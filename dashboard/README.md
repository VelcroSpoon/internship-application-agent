# Dashboard

The review UI for the internship agent. Reads and writes go through the Python
service; this app holds no data of its own.

```bash
uv run python -m internship_agent serve   # from the repo root, first
npm install
npm run dev                                # then open http://localhost:3000
```

Set `API_BASE` if the service is not on `http://127.0.0.1:8000`.

See the [project README](../README.md) for what it does and why it is built this way.
