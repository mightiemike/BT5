### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When any user routes through `MetricOmmSimpleRouter`, that `sender` is the router contract, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every unprivileged user the ability to bypass the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`sender` is set to `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So the pool sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**The dilemma this creates for pool admins:**

| Admin configuration | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Router NOT allowlisted | Cannot use router at all | Correctly blocked |
| Router IS allowlisted | Can use router | **Also bypass the allowlist via router** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The `extensionData` field is passed through but `SwapAllowlistExtension` ignores it entirely, so there is no in-band mechanism to recover the originating user identity.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific participants (e.g., approved market makers, KYC'd addresses, or protocol-controlled accounts) is fully bypassable by any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost beyond normal gas. Consequences include:

- Unauthorized users trading on pools designed for curated participants, extracting value from LPs whose positions were priced assuming a controlled counterparty set.
- Regulatory or compliance-gated pools losing their access control guarantees.
- LP principal loss if the pool's oracle-driven pricing model assumes only specific, non-adversarial counterparties.

This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality" impact categories.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract intended for real deployment.
- Any pool admin who wants allowlisted users to access the router must allowlist the router, which is the natural and expected configuration.
- The bypass requires only a standard `exactInputSingle` call through the public router — no special privileges, no flash loans, no complex setup.
- Likelihood is **Medium**: it requires a pool to be configured with the allowlist extension and the router to be allowlisted, but this is the expected operational state for any allowlisted pool that supports the periphery.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the originating user, not the intermediary caller. Two approaches:

1. **Extension-data identity forwarding**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks that address. This requires a coordinated change to the router and extension.

2. **`tx.origin` check (with caveats)**: Replace `sender` with `tx.origin` in the allowlist lookup. This correctly identifies the EOA initiating the transaction but is incompatible with smart-contract wallets and multicall patterns.

3. **Separate per-user router allowance**: Introduce a two-level check — gate the router at the pool level, and require the router itself to enforce a per-user allowlist before forwarding to the pool.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)      // required for router-mediated swaps
4. Non-allowlisted userB calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., ...})
5. Router calls pool.swap(...) with msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true.
7. userB's swap executes successfully — allowlist bypassed.
8. Direct call: userB calls pool.swap() directly →
       allowedSwapper[pool][userB] → false → NotAllowedToSwap (correctly blocked).
```

The bypass is exclusive to the router path. Direct pool calls are correctly gated. This confirms the root cause is the wrong-actor binding in `SwapAllowlistExtension.beforeSwap`.