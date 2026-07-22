Looking at the codebase, I need to find where a configured guard is bypassed or misapplied in one code path but correctly applied in another — the direct analog to the LES bug.

Let me trace the swap allowlist guard through the router path.