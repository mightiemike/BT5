### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any caller to bypass a per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for permitted users), every public caller of the router can bypass the allowlist and trade in a pool that was intended to be restricted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the identity check as follows:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (used as the mapping key). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is always `msg.sender` of the pool's `swap` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

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
```

So `msg.sender` to the pool is the **router**, and `sender` passed to `beforeSwap` is the **router address**. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to allow specific users to swap through the router has no mechanism to do so selectively. Their only option is to allowlist the router itself — which opens the gate to **every caller** of the public router, including non-allowlisted addresses.

The same identity mismatch exists for `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional traders, or whitelisted market makers) is fully bypassed once the pool admin allowlists the router. Any public address can call `MetricOmmSimpleRouter` and trade in the restricted pool. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to unrestricted adverse selection, which directly erodes LP principal. This fits the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" criterion.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a natural and expected configuration step for any pool that wants to support router-mediated swaps for its permitted users. The admin has no other way to enable router access for specific users; the only knob is the router address itself. The mistake is not malicious; it is a predictable consequence of the extension's design. Once the router is allowlisted, the bypass requires no special privilege: any EOA calls `exactInputSingle` on the public router.

---

### Recommendation

The extension must receive and check the **original end-user identity**, not the intermediary's address. Two complementary fixes:

1. **Pass the original caller through `extensionData`**: The router should encode `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender` for swap allowlists**: For many use cases the intended gate is the economic beneficiary (`recipient`), not the technical caller. The extension could be parameterised to choose which field to gate.

A minimal diff for option 1 in the extension:

```diff
-function beforeSwap(address sender, address, bool, ...)
+function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
     external view override returns (bytes4)
 {
+    address effectiveSender = extensionData.length >= 20
+        ? abi.decode(extensionData, (address))
+        : sender;
-    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
         revert IMetricOmmPoolActions.NotAllowedToSwap();
     }
```

And in the router, encode `msg.sender` as the first word of `extensionData` before forwarding.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** allowlist `attacker` EOA: `allowedSwapper[pool][attacker] == false`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router] == true` → passes.
7. The swap executes. `attacker` has traded in a pool they were never meant to access.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
