Fix `acquire_lock` so a lock containing a PID that no longer exists is treated as stale and replaced, while a live owner's lock remains protected. Use only the standard library.
