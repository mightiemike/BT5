### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps by swapper address. However, when users swap through `MetricOmmSimpleRouter`, the `sender` argument the extension receives is the **router's address**, not the actual user's address. If the pool admin allowlists the router (the only way to enable router-based swaps for legitimate users), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. [1](#0-0) 

The pool's `swap()` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` of `pool.swap()` = **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | **Every user** can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows router-based swaps for allowlisted users while blocking non-allowlisted users. The extension's NatSpec states it "Gates `swap` by swapper address, per pool," but the implementation gates by the **immediate caller's** address, which is the router when the router is used. [4](#0-3) 

---

### Impact Explanation

Any user can bypass the swap allowlist of a curated pool by routing through `MetricOmmSimpleRouter`. Curated pools may be configured with favorable pricing, restricted counterparties, or institutional access controls. Unauthorized swaps can:

- Extract value from LPs through favorable oracle-priced swaps that were intended only for vetted counterparties.
- Break the pool's intended access model entirely, rendering the allowlist extension ineffective.

This is a direct broken-core-functionality impact: the configured guard fails open for all router-mediated swaps once the router is allowlisted.

---

### Likelihood Explanation

- The pool must use `SwapAllowlistExtension` (a supported periphery extension).
- The pool admin must allowlist the router — a natural and expected action for any pool that wants to support the official periphery router.
- Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges.

Likelihood: **Medium** (requires the router to be allowlisted, which is the expected operational setup for router-compatible curated pools).

---

### Recommendation

The extension must identify the **actual economic actor**, not the immediate caller. Two viable approaches:

1. **`extensionData` attestation**: Have `MetricOmmSimpleRouter` encode the actual `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a router-signed or factory-attested format).
2. **Separate `actualSwapper` parameter**: Extend the `beforeSwap` hook signature or use a dedicated field so the router can attest the real user's address in a tamper-proof way.

Until fixed, pool admins using `SwapAllowlistExtension` should be warned that allowlisting the router grants unrestricted access to all router users.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router to enable router swaps
4. UserB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router.
6. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. UserB's swap executes successfully despite not being on the allowlist.
```

The root cause is at `SwapAllowlistExtension.sol:37` where `sender` (the router) is checked instead of the actual user, and at `MetricOmmSimpleRouter.sol:72-80` where no mechanism exists to attest the real caller's identity to the extension. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
