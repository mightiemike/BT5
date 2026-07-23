### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the direct caller, so `sender` = router address. If the pool admin allowlists the router (a natural configuration to enable router-mediated swaps for permitted users), every unpermitted user can bypass the allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap` directly, making the router the `msg.sender` the pool sees:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

There is no mechanism for the router to forward the original `msg.sender` to the extension in a trustworthy way; `extensionData` is fully user-controlled and cannot be relied upon for identity.

Consequence: the extension cannot distinguish between different users going through the same router. Allowlisting the router is equivalent to `allowAllSwappers = true` for every user who can reach the router — which is the entire public.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., restricted to KYC'd counterparties or specific market makers) and configures `SwapAllowlistExtension` with individual permitted addresses will naturally also allowlist the router so that permitted users can enjoy router UX (slippage protection, multi-hop, deadline checks). The moment the router is allowlisted, every unpermitted address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension passes unconditionally. The curated pool's access control is completely nullified. Unpermitted users gain access to oracle-priced liquidity, can arbitrage against the pool's LPs, and can drain LP principal at favorable prices — a direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router, which is a routine and expected configuration step for any curated pool that intends to support standard periphery UX. The router is a public, immutable contract; any user can call it. No privileged access, no malicious setup, and no non-standard token behavior is required. The trigger is a single public `exactInputSingle` call from any EOA.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the direct pool caller. Two viable approaches:

1. **Require the original user's address in `extensionData` with a pool-level signature or permit**: the extension verifies a signed attestation that the embedded address is the true initiator. The router must be updated to include this attestation.
2. **Remove router allowlisting from the model entirely**: document that `SwapAllowlistExtension` only supports direct `pool.swap` calls and that allowlisting the router defeats the guard. Provide a separate router-aware extension that reads the original user from a trusted transient-storage slot written by the router before calling the pool.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, user_A, true)   // intended permittee
3. Admin calls setAllowedToSwap(pool, router, true)   // to let user_A use the router
4. user_B (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          pool,
           recipient:     user_B,
           zeroForOne:    true,
           amountIn:      X,
           extensionData: ""
       })
5. Router calls pool.swap(user_B, true, X, ...) — msg.sender to pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Swap executes; user_B receives output tokens from the curated pool.
```

`user_B` was never allowlisted. The guard passed because the router's address — not `user_B`'s address — was the identity checked by the extension. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
