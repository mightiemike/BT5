### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist evaluates the **router's address** rather than the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every address on-chain the ability to bypass the swap allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

At this point `msg.sender` to the pool is the **router contract**, not the end-user. The allowlist therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every router-mediated swap reverts `NotAllowedToSwap`, even for individually allowlisted users — core swap path broken |
| Router **allowlisted** (required to unblock users) | Every address on-chain can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — which is the only way to let legitimate users swap through the standard periphery — the allowlist is rendered completely ineffective. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps in a pool that was designed to be access-controlled. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's access control configuration is bypassed by an unprivileged path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract that end-users are expected to use. A pool admin who deploys a swap-allowlisted pool and wants their allowlisted users to be able to swap will naturally allowlist the router. The bypass is then immediately reachable by any address with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end-user), not the intermediary contract. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: forward the original `msg.sender` (the end-user) as an authenticated field inside `extensionData` so the extension can recover it. The extension would then decode and verify the user identity from `extensionData` rather than relying on the raw `sender` argument.

2. **In `SwapAllowlistExtension`**: if the `sender` is a known router/intermediary, decode the real user from `extensionData` and gate on that address instead. Alternatively, document that `sender` is the direct pool caller and require integrators to call the pool directly when the allowlist is active.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly supplied by the caller), which the liquidity adder correctly forwards as the end-user's address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended allowed user)
  - allowedSwapper[pool][bob]   = false  (bob is explicitly NOT allowed)
  - allowedSwapper[pool][router] = true  (admin must set this so alice can use the router)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓
    → swap proceeds — bob's swap is NOT blocked

Result:
  bob, a non-allowlisted address, successfully executes a swap in a
  pool whose allowlist was configured to exclude him.
  The allowlist invariant is broken with zero privilege escalation.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
