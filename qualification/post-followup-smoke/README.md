# Post-follow-up frozen smoke

The smoke compared the bounded reviewer immediately before issue #86 with the
post-#86 candidate on the same formatted version of the original unsafe complex
FileCounter patch. The DGX model was warmed first, both trials were sequential,
and load time was excluded.

The deterministic preflight first rejected the original historical commit before
inference because it contained trailing whitespace. That validates the issue #87
boundary. Both arms were then moved to the next historical formatting-only commit,
which retained the direct `os.sync()` and unguarded `os.O_DIRECTORY` defects.

The control approved the patch, missed both defects, and emitted malformed JSON.
The candidate explicitly noticed both defects and initially reasoned toward
requesting changes, but reversed itself and published an incorrect approval. It
used 18,783 local tokens and 46.847 seconds end-to-end versus 6,627 tokens and
23.791 seconds for the control.

The complete smoke—from durable issue creation through Codex adjudication and
full regression validation—reached its decision in 283 seconds (4m43s).

Decision: **revise; do not promote**. The candidate improves evidence attention
and terminal schema reliability, but not the correctness metric that matters.
Codex intervention remains necessary, and the local reviewer remains advisory.
