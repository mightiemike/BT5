### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the value the pool passes from its own `msg.sender` at the time `pool.swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (a natural action to enable router-based swaps for their curated user set), every unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to extensions:**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
```

**`SwapAllowlistExtension` keys the check on that `sender`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` directly, making itself `msg.sender`:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]
    );
```

When this call reaches the pool, `msg.sender == router`. The pool passes `router` as `sender` to `_beforeSwap`, which passes it to `SwapAllowlistExtension`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Two broken outcomes result:**

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Individually allowlisted users cannot swap through the router — unusable swap flow |
| Router **allowlisted** (to enable router-based swaps) | Every unprivileged user bypasses the allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs) cannot enforce that restriction when the standard periphery router is involved. If the admin allowlists the router to enable router-based swaps for their users, the allowlist is completely nullified: any address can call `MetricOmmSimpleRouter.exactInput` or `exactInputSingle` and execute swaps against the restricted pool. This constitutes an admin-boundary break where an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access-control guard, and broken core swap functionality (allowlisted users cannot use the router without the bypass side-effect).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool operator who configures a `SwapAllowlistExtension` and also wants their users to use the router will naturally allowlist the router, triggering the bypass. The attacker requires no special privileges — only the ability to call the public router.

---

### Recommendation

The `SwapAllowlistExtension` should not rely on the `sender` argument supplied by the pool (which reflects the direct caller, i.e., the router). Instead, the extension should gate on the **economically relevant actor**. Two approaches:

1. **Router forwards real user identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and verifies it (with the router itself being a trusted forwarder, verified via `msg.sender` to the extension).
2. **Extension checks `tx.origin` as a fallback for router-mediated calls**: Less clean but avoids the router-coupling problem; only viable if the threat model excludes contract-based callers.
3. **Pool passes `tx.origin` or a separate `payer` field**: The pool interface is extended to carry the original initiator separately from the direct caller.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to let allowlisted users reach the pool via the router.
3. Non-allowlisted attacker calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: restrictedPool,
           tokenIn: token1,
           tokenOut: token0,
           recipient: attacker,
           amountIn: X,
           ...
       })
4. Router calls pool.swap(...) with msg.sender == router.
5. Pool calls _beforeSwap(router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Swap executes. Attacker receives token0 from a pool they were never
   individually authorized to trade against.
```

The allowlist is fully bypassed. Any user who calls the router against a pool that has allowlisted the router address can trade, regardless of whether their own address is on the allowlist.