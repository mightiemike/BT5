Looking at the external report's vulnerability class — **a loop over multiple reward configs where one failing transfer reverts the entire reward claim, locking all rewards** — I need to find a structural analog in this Cosmos SDK chain.

Let me trace the reward settlement dependency chain in `x/tieredrewards`.