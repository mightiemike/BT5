### Title
`SwapAllowlistExtension` Receives Router Address as `sender`, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every `beforeSwap` extension hook. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the **router contract**, not the originating user. `SwapAllowlistExtension.beforeSwap()` therefore checks the router's allowlist status rather than the actual trader's, making the allowlist guard trivially bypassable by any user who routes through the public router.

---

### Finding Description

`MetricOmmPool.swap()` constructs the `_beforeSwap` call as:

```solidity
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
``` [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this `sender` value verbatim to every configured extension:

```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, with no mechanism to forward the originating user's address:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [3](#0-2) 

The `_setNextCallbackContext` call records the real user only for the **payment callback** (`metricOmmSwapCallback`). The extension hook path is entirely separate and receives only `msg.sender` of the pool call — the router address.

`SwapAllowlistExtension.beforeSwap()` receives `sender = address(router)` and evaluates the allowlist against that address. Two exploitable outcomes follow:

1. **Allowlist bypass (primary impact):** If the router is allowlisted as a trusted intermediary (the natural operational setup), any non-allowlisted user can call `router.exactInputSingle()` and the extension will approve the swap because it sees the allowlisted router address, not the blocked user.

2. **Allowlisted users locked out:** If the router is not allowlisted, legitimate allowlisted users cannot trade through the router at all, breaking the primary user-facing swap path.

The `IMetricOmmExtensions.beforeSwap` interface confirms `sender` is the first parameter the extension receives and is the identity it must gate on: [4](#0-3) 

The project's own audit research notes explicitly flag this concern: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Impact Explanation

Pool operators deploy `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers). The bypass means any address — including those explicitly excluded — can trade against the pool's LP positions through the public router. LP providers in such pools suffer unauthorized exposure: their capital is consumed by trades they contractually restricted, potentially at unfavorable oracle prices or in volumes they did not intend to permit. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any user who discovers the bypass (or any MEV bot scanning for restricted pools) can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The router is a public, permissionless contract. Likelihood is high.

---

### Recommendation

The pool must forward the originating user's identity through the extension call chain. Two approaches:

1. **Pass the real initiator explicitly:** Add an `initiator` field to the `beforeSwap` / `afterSwap` extension interface, populated by the router before calling `pool.swap()` (e.g., via a transient-storage slot similar to the existing callback context mechanism). The extension then gates on `initiator` rather than `sender`.

2. **Gate on `recipient` as a proxy:** If the router always sets `recipient` to the actual user, extensions can check `recipient` instead of `sender`. This is fragile (recipient can be set to any address) and is not recommended as a primary fix.

3. **Require direct pool interaction for allowlisted pools:** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router, and users must call `pool.swap()` directly. This breaks UX but closes the bypass.

---

### Proof of Concept

```
Setup:
  - Deploy MetricOmmPool with SwapAllowlistExtension configured.
  - allowAll = false; allowedSwapper[pool][routerAddress] = true (router allowlisted as trusted intermediary).
  - blockedUser is NOT in the allowlist.

Attack:
  1. blockedUser calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, zeroForOne, ...) — msg.sender at pool = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. ExtensionCalling dispatches to SwapAllowlistExtension.beforeSwap(sender=router, ...).
  5. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
  6. blockedUser's swap executes against LP positions despite being explicitly excluded.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; LP positions consumed by unauthorized trader.
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
