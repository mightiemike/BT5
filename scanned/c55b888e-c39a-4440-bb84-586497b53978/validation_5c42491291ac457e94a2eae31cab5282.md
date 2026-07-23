Looking at the codebase, I need to trace the identity that `SwapAllowlistExtension` actually checks when a swap goes through `MetricOmmSimpleRouter`, versus what the pool admin intends to gate.

Let me read the key files to confirm the identity mismatch.