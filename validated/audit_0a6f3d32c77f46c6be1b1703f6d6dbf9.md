### Title
`SwapAllowlistExtension` gates the router's address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin adds the router to the allowlist (the natural setup for any pool that wants to support router-mediated swaps), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call path that exposes the bug**

1. `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called by an arbitrary user.
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — `msg.sender` inside the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`. The actual end user's address is never consulted.

**Why the router ends up on the allowlist**

A pool configured with `SwapAllowlistExtension` that also wants to support router-mediated swaps must add the router to the allowlist. There is no other mechanism: the extension has no way to inspect the original EOA behind the router call. Once `allowedSwapper[pool][router] = true`, the guard is permanently open to every user who calls any of the four router entry points.

**Contrast with the deposit path**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner), not `sender` (the payer). When `MetricOmmPoolLiquidityAdder` is used, `sender = adder` but `owner` is still the caller-supplied position owner. Because LP shares go to `owner`, an attacker who sets `owner` to an allowed address gains nothing (they pay tokens but the shares go elsewhere). That path does not produce a useful bypass. The swap path is the exploitable one.

---

### Impact Explanation

A pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified traders, institutional desks, or whitelisted strategies) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The unauthorized user can:

- Execute swaps against the pool's liquidity at oracle-derived prices, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade.
- Drain one side of the pool's liquidity if the oracle price is favorable, leaving LPs with an imbalanced position they cannot exit at the expected value.

This is a direct loss of LP principal caused by a broken core access-control invariant.

---

### Likelihood Explanation

The trigger requires the pool admin to add the router to the allowlist. This is not a malicious or unusual action — it is the only way to enable router-mediated swaps on an allowlisted pool. Any pool that (a) uses `SwapAllowlistExtension` and (b) wants users to be able to swap via the standard periphery router will reach this state. The pool admin may not realize that adding the router address is equivalent to disabling the per-user check entirely, because the extension's NatSpec ("Gates `swap` by swapper address, per pool") implies individual-user granularity that the implementation cannot deliver when a router is involved.

---

### Recommendation

The extension must gate the **economic actor**, not the **direct caller**. Two viable approaches:

1. **Require the router to forward the originating user in `extensionData`**: The extension decodes a user address from `extensionData` and checks that address. The router must be updated to encode `msg.sender` into `extensionData` before calling `pool.swap`. This requires coordinated changes to both the router and the extension.

2. **Remove the router from the per-user allowlist and instead allowlist individual users only for direct `pool.swap` calls**: Document clearly that router-mediated swaps are incompatible with `SwapAllowlistExtension` unless the router is trusted to enforce its own access control.

---

### Proof of Concept

```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E configured in BEFORE_SWAP_ORDER.
2. Pool admin calls E.setAllowedToSwap(P, router, true)
   — intended to enable router-mediated swaps for allowed users.
3. Pool admin calls E.setAllowedToSwap(P, alice, true)
   — alice is the only intended individual swapper.
4. Bob (not on the allowlist) is supposed to be blocked.

Attack
──────
5. Bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: P,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
   });

6. Router calls P.swap(bob, true, X, ...) with msg.sender = router.

7. P._beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)

8. Check: allowedSwapper[P][router] == true  →  passes.

9. Swap executes. Bob receives output tokens. LPs bear the trade.

Result
──────
Bob, who is not on the individual allowlist, successfully swaps against the
restricted pool. The allowlist invariant is broken. Any user can repeat this
for any amount up to the pool's liquidity.
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
