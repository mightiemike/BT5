### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, allowing any caller to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` uses the `sender` argument (which the pool sets to `msg.sender` of `pool.swap()`) as the identity to check against the allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the per-user gate by calling any `exact*` function on the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**Step 2 — Extension checks that value against the allowlist.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

**Step 3 — Router is the immediate caller of `pool.swap()`.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router calls `pool.swap()` directly; `msg.sender` inside the pool is the router, so `sender` forwarded to the extension is the router address.

**Step 4 — Bypass.**

A pool admin who wants to allow router-mediated swaps for allowlisted users must call:

```
swapExtension.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the originating EOA is. Any non-allowlisted user can call `router.exactInputSingle(...)` and the extension passes.

The same path applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` with `msg.sender == router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) is fully open to any caller the moment the pool admin allowlists the router. The attacker can execute swaps at oracle-derived prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes a broken core pool functionality (the allowlist guard fails open) and an admin-boundary break where an unprivileged path bypasses the pool admin's intended access control.

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be reachable. This is a natural and expected operational step: any pool that wants to support the standard periphery flow for its allowlisted users must grant the router entry. The design gives the admin no way to simultaneously allow router-mediated swaps for specific users and block them for others, so the bypass is an inevitable consequence of enabling router support on a curated pool.

---

### Recommendation

The extension must gate on the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated change in the router and the extension.

2. **Check `sender` only when it is not a known router; otherwise decode the real user from `extensionData`.** The extension can maintain a registry of trusted forwarders and require them to supply the originating address in the payload.

Either way, the extension must not treat the router address as the identity to gate.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended gated user
  allowedSwapper[pool][router] = true         // admin enables router for alice's convenience

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
      → pool.swap(msg.sender = router)
        → _beforeSwap(sender = router, ...)
          → SwapAllowlistExtension.beforeSwap(sender = router)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, bob receives output tokens

Result: bob swaps successfully despite never being allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
