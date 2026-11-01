# Shared local memory

This directory operates a local SurrealDB knowledge store. See README.md for commands.
Read PORTS.md before configuring listeners or PC forwarding. Check other projects'
reservations as well as active listeners; a free socket is not an unreserved port.
Never commit or print .env, .local/, data/, backups/, passwords, or authorization headers.
Preserve existing records; schema changes must be additive unless explicitly authorized.
Read MEMORY.md for the knowledge conventions shared with agent clients.
