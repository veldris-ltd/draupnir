# deploy

Stage 4 of the pipeline in SAD 11H, run on the self hosted runner on ALVISS.

| Script | Stage | Notes |
|---|---|---|
| `rollout.sh` | 4.2 | Pulls the signed image per unit and restarts the rootless systemd units |
| `rollback.sh` | 4.4 | Returns the units to the previous revision. The schema stays forward |

The units are rootless Podman containers under the user systemd instance,
matching AC-Q7 and SAD 11.1. The scripts take a revision rather than reading
one, so a manual rollback from a terminal is the same operation the pipeline
performs.
