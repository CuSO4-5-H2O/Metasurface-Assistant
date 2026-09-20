# Metasurface Assistant v1.0

A Codex skill for active and programmable metasurfaces, metasurface antennas,
RIS, reflectarrays, PIN/varactor state models, CST multi-state simulation,
phase-codebook evaluation, and bounded electromagnetic optimization.

## Contents

- `SKILL.md`: task routing and execution policy.
- `scripts/`: study orchestration, metrics, reports, array coding, and pymoo
  candidate generation.
- `references/`: theory, evaluation contracts, active-device guidance, CST
  integration, and study schema.
- `tests/`: synthetic and orchestration regression tests.

## Install

Place this repository in the Codex skills directory as
`metasurface-assistant`, then restart or refresh Codex skill discovery.

## Validate

```powershell
python -m pytest tests -q
```

The skill deliberately keeps device-specific physical parameters sourced from
the current task, project configuration, datasheet, or traceable literature.
It does not invent universal PIN, varactor, gain, or bias-network values.
