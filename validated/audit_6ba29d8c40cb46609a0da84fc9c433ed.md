Looking at the vulnerability class — **fee collected during a trade/LP operation is not credited to the LP pool's accounting variable, causing LP token holders to lose value** — I need to find the same pattern in Nado.

Let me examine the `burnNlp()` fee accounting and the `claimSequencerFees()` function more carefully.