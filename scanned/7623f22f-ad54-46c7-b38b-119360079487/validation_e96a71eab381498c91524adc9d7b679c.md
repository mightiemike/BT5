Looking at the codebase, I need to trace the extension guard and hook plumbing to find an analog to the Karak "deposit during pending slash" bug — specifically, a configured guard that is misapplied or omitted for one action while protecting another, with fund-impacting consequences.

Let me read the key extension files and the pool's liquidity/swap guard logic.