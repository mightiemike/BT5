### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. A pool admin who allowlists the router (required for any router-mediated swap to succeed on an allowlisted pool) inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first argument) is on the allowlist, keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The router never forwards the originating user's address as `sender`; there is no mechanism in the pool or extension interface to do so.

**Consequence — two broken states:**

| Pool admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router at all (their direct-call allowance is irrelevant; the router's address fails the check). |
| Router **allowlisted** (required to enable router-mediated swaps) | Every address on the network can bypass the allowlist by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). |

The second state is the direct fund-impacting bypass: the pool admin believes only specific counterparties can trade, but any EOA or contract can route through the public `MetricOmmSimpleRouter` and the extension passes because `allowedSwapper[pool][router] == true`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting swap access to specific counterparties (e.g., KYC'd addresses, protocol-owned contracts, or whitelisted market makers). Once the router is allowlisted — the only way to make router-mediated swaps work — the guard is completely neutralised. Any user can execute swaps against the pool's liquidity, draining LP value through arbitrage or front-running that the allowlist was designed to prevent. This is a direct loss of LP principal and protocol fees above Sherlock thresholds.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented in the periphery. Any pool operator who deploys a `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router must allowlist the router address. The router is a public, permissionless contract. The bypass requires no special privileges, no flash loans, and no unusual token behaviour — a single `exactInputSingle` call suffices.

---

### Recommendation

Pass the **originating user** through the hook, not the immediate caller. Two complementary fixes:

1. **Router-side**: Store the originating `msg.sender` in transient storage alongside the callback context and expose it in the `extensionData` payload so extensions can read it.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should check the `recipient` or a user address extracted from `extensionData` rather than the raw `sender` when the immediate caller is a known router.

Alternatively, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by preventing co-registration.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedSwapper[pool][alice]  = true   // alice is the intended gated user
  allowedSwapper[pool][router] = true   // required so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool:      pool,
        recipient: bob,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(bob, true, X, ...)   // msg.sender = router
        → _beforeSwap(router, bob, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result: bob bypasses the allowlist and swaps against pool liquidity.
``` [2](#0-1) [1](#0-0) [4](#0-3)

### Citations

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
