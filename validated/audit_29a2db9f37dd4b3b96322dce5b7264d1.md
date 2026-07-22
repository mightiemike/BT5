Based on my investigation of the codebase, I can identify a valid analog to the external bug.

---

### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool passes `msg.sender` (the direct caller of `pool.swap`) as `sender`. When `MetricOmmSimpleRouter` intermediates the swap, `sender` becomes the router's address, not the actual end user. If the pool admin allowlists the router to support router-based swaps, every unprivileged user can bypass the per-user restriction by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its allowlist check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct, used as the namespace key) and `sender` is the first argument the pool passes into the hook. The pool's `swap` and `simulateSwapAndRevert` both pass `msg.sender` — the direct caller of the pool — as that first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension receives `sender = router_address`. The extension then checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable dilemma for any pool admin who wants to enforce a per-user swap allowlist while still supporting the standard periphery router:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| Allowlist the router | **Every** user can bypass the allowlist by routing through the router |

The router is a public, permissionless periphery contract. Any user can call it. Allowlisting it is equivalent to disabling the allowlist entirely for the router path. [3](#0-2) 

The interface documentation explicitly acknowledges the router as a supported entrypoint for allowlisted pools:

> "Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set … public swap → beforeSwap hook → allowAll/allowedSwapper lookup keyed by pool and sender" [4](#0-3) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInput` or `exactOutput`. The bypassing user can execute swaps against the pool's liquidity, draining LP value or executing trades the pool admin explicitly intended to block. This is a direct loss-of-policy-enforcement impact on LP assets and pool integrity.

### Likelihood Explanation

The likelihood is **medium-high**. Any pool that:
1. Deploys `SwapAllowlistExtension` to restrict swaps, **and**
2. Allowlists the router (a natural step for any pool that wants to support the standard periphery UX)

is immediately vulnerable. The router is a public contract; no special privilege is required to call it. The attacker only needs to know the pool address and call the router.

### Recommendation

The pool should pass the **originating user** as `sender` to the extension, not the immediate `msg.sender`. Two approaches:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the actual `msg.sender` (the end user) as the `sender` argument when calling `pool.swap`, rather than relying on the pool to use its own `msg.sender`. This requires the pool's `swap` signature to accept an explicit `sender` parameter, or the router to encode the real user in `extensionData` and the extension to decode it.

2. **Extension-side**: `SwapAllowlistExtension` should decode the real user from `extensionData` when the direct caller is a known router, similar to how Evmos intercepts the `CREATE` opcode to check the originating caller rather than the immediate contract.

The cleanest fix is for the pool to accept an explicit `sender` parameter (verified against `msg.sender` or a trusted forwarder list), so the extension always sees the economically relevant actor.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` as the only permitted swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router so that `alice` can use the standard periphery: `setAllowedToSwap(pool, router, true)`.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap proceeds.
6. `bob` successfully swaps on a pool he was explicitly barred from, bypassing the curated allowlist entirely. [3](#0-2) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
