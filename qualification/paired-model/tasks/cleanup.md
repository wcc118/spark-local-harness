Fix `write_then_fail` so the partial file is removed whenever the operation fails, while preserving the original exception. Do not suppress errors.
