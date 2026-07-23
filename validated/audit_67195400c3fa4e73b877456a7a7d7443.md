### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool` sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` routes a swap, it is the direct caller, so the extension checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. Any pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently grants swap access to every user who calls through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards that value unchanged as the `sender` parameter to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender` (the direct pool caller), not the originating user.**

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

Here `msg.sender` is the pool (correct — enforced by `onlyPool`), and `sender` is whoever called `pool.swap()`. When the router is the caller, `sender` is the router address.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, substituting itself as `msg.sender`.**

`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap()` without forwarding the originating user's address: [4](#0-3) [5](#0-4) 

The pool therefore receives `msg.sender = router` and passes the router address as `sender` to the extension. The allowlist check becomes `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through the router.

**Step 4 — Contrast with `DepositAllowlistExtension`, which correctly checks `owner`.**

The deposit extension checks the `owner` argument (the economic beneficiary of the position), not `sender` (the adder contract). This is correct because `owner` is explicitly supplied and represents the gated identity: [6](#0-5) 

The swap extension has no equivalent mechanism — it has no way to recover the originating user from the arguments it receives.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of users (e.g., KYC'd counterparties) is fully bypassed the moment the router is allowlisted. Because the router is the standard periphery entry point, a pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Doing so grants swap access to every address on the network that calls through the router, completely defeating the curated-pool access control. Funds flow to and from the pool from unapproved counterparties, breaking the core invariant the extension was deployed to enforce.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the documented, supported periphery path for swaps. Any operator of a curated pool who also wants to support the standard router faces a forced choice: either block all router-mediated swaps (breaking UX for allowlisted users) or allowlist the router (opening the pool to everyone). The misconfiguration is the only practical path to supporting both goals, making exploitation straightforward for any non-allowlisted user.

---

### Recommendation

The `SwapAllowlistExtension` must gate the originating user, not the direct pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address when the direct caller is a known router. This requires a trusted router registry or a signed-user pattern.

2. **Check `sender` only when it is not a known intermediary, and require the extension to be paired with a router that forwards user identity**: Define a standard interface where the router passes the originating user as a typed field in `extensionData`, and the extension decodes it unconditionally. This is the cleanest fix because it does not require a router registry.

In either case, the extension must never treat the router's address as the identity to gate.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓
          → swap proceeds

Result:
  bob swaps successfully on a pool that was supposed to restrict
  swapping to alice only. The allowlist is fully bypassed.
```

The root cause is at: [7](#0-6) 

where `sender` is the router address, not the originating user, whenever the swap enters through `MetricOmmSimpleRouter`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
