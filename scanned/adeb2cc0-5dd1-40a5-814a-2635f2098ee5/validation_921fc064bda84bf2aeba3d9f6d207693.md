### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Guard — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for router-mediated swaps to work), every user on the network can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of swap(), not the end user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making itself the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool therefore passes `sender = address(router)` to the extension. The allowlist check becomes `allowedSwapper[pool][router]`. For any user to swap through the router on an allowlisted pool, the admin must add the router to the allowlist. Once the router is allowlisted, the check is satisfied for every caller of the router — including addresses the admin never intended to permit — because the router is a public, permissionless contract.

The `extensionData` field is user-controlled but the extension never reads it, so there is no in-band mechanism to pass the real user identity.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool. Unauthorized traders can extract value from LPs by trading at oracle prices the pool was not designed to offer them, constituting a direct loss of LP principal and a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard periphery entry point documented for end users. Any pool admin who wants users to interact via the router must allowlist it. The bypass is therefore reachable in every realistic production deployment of `SwapAllowlistExtension` that supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — a single public call to the router suffices.

---

### Recommendation

The extension must gate the actual end user, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is the end user; however this breaks for multi-hop paths where intermediate recipients are the router itself.

2. **Require the router to embed the real user in `extensionData`** and have the extension decode and verify it. This requires a coordinated change to the router and the extension, and the extension must also verify that the embedding contract is trusted.

3. **Allowlist at the router level** — the router enforces its own per-user allowlist before calling the pool, and the pool-level extension only allowlists the router. This moves the trust boundary to the router, which must then be audited as a gating contract.

The simplest safe fix is option 3 combined with a dedicated allowlist-aware router variant, so the pool-level extension never needs to trust user-supplied identity claims.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  pool admin calls setAllowedToSwap(pool, router, true)      // required for router-mediated swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        ...
        extensionData: ""
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, ..., extensionData)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (check passes)
        → swap executes, bob receives output tokens

  Result: bob bypasses the allowlist and trades against the pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
