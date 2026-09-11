# samples — synthetic demo data

**Not real lab data.** Generated for demonstration only (fictitious batch `DEMO-T1`).

- `core/` — a minimal data store (batches/samples/runs/steps/measurements/observations).
  Point the backend at it:

  ```bash
  OPENNANO_CORE_DIR=$(pwd)/samples/core uvicorn main:app --port 8000
  ```

  Then try: `GET /api/core/stats`, `GET /api/core/wide?quantity=depth_center_nm&stage=ICP`,
  or ask the agent "what depth values exist on the demo ICP tool?".
