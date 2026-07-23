### Title
Swap Allowlist Bypassed via Router: `sender` Identity Mismatch Lets Any User Swap on Permissioned Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to work), every user of the router bypasses the per-user allowlist, regardless of whether they are individually permitted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// SwapAllowlistExtension.sol L31-41
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

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap(...)`. [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [2](#0-1) 

The pool then dispatches `_beforeSwap(msg.sender, recipient, ...)` where `msg.sender` is the router:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,       // <-- router address, not the actual user
    recipient,
    zeroForOne,
    ...
    extensionData
);
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (every router swap reverts).
- **Allowlist the router** → every user of the router, including non-allowlisted ones, passes the gate.

There is no configuration that allows specific users to swap through the router while blocking others.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Once the router is allowlisted (which is required for any allowlisted user to use it), non-allowlisted users can execute swaps against the pool's liquidity, receiving tokens they are not authorized to receive. This constitutes unauthorized swap execution on a permissioned pool, with direct fund-flow consequences: the pool's token reserves are consumed by trades the admin intended to block.

---

### Likelihood Explanation

The trigger is unprivileged and requires no special role. Any user who knows the pool address and the router address can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool. The precondition — that the router is allowlisted — is a natural operational step any pool admin would take to let their legitimate users access the router. The bypass is therefore reachable in any realistic deployment where the router is used alongside the allowlist extension.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor — the end user — not the intermediary contract. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `sender` only when `sender` is not a known trusted router; alternatively, require the router to forward the originating user address in `extensionData` and decode it here.

2. **In `MetricOmmSimpleRouter`**: forward the originating `msg.sender` (the actual user) as part of `extensionData` on every `pool.swap` call, so allowlist extensions can recover the true caller identity.

The cleanest fix is option 2: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for alice to use the router.

Attack:
  - Charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool:          <target pool>,
          recipient:     charlie,
          zeroForOne:    true,
          amountIn:      X,
          ...
      })

  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] == true  → passes.
  - Charlie's swap executes; he receives pool tokens he is not authorized to receive.
``` [4](#0-3) [5](#0-4) [3](#0-2) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
