### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap` call is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every user — including those the pool admin intended to block — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for pool-identity namespacing) and `sender` is whoever called `pool.swap(...)`. When the user enters through `MetricOmmSimpleRouter.exactInputSingle` (or any other `exact*` entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` of `pool.swap` is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The router does not forward the original caller's identity to the pool in any way; the pool has no mechanism to receive it. The pool's `swap` signature accepts only `recipient`, not a separate `payer` or `originator`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

1. **Do not allowlist the router** → allowlisted users cannot use the standard periphery at all; they must call `pool.swap` directly and implement their own callback.
2. **Allowlist the router** → the allowlist is completely bypassed for every user who routes through the router. Any non-allowlisted address calls `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension sees `allowedSwapper[pool][router] = true`, granting the swap.

Scenario 2 is the realistic operational path: pool admins who deploy a curated pool and also want users to use the standard periphery will allowlist the router. At that point every user on the network can trade on the pool regardless of their individual allowlist status. This directly breaks the core pool functionality the extension was deployed to enforce and constitutes a policy bypass with fund-impacting consequences (non-KYC'd or otherwise restricted counterparties execute swaps the pool was designed to reject).

---

### Likelihood Explanation

The trigger requires only that:
- A pool is deployed with `SwapAllowlistExtension` in its `beforeSwap` order (a supported, documented configuration).
- The pool admin allowlists the router address to permit standard periphery usage (the natural operational step).
- A non-allowlisted user calls any `exact*` function on `MetricOmmSimpleRouter`.

All three conditions are reachable by any unprivileged user once the pool is live. No special role, malicious setup, or non-standard token is required.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original caller through the pool**: Add an optional `originator` field to the swap interface (or use `extensionData` as a signed attestation) so the pool can forward the true initiator to extensions.
2. **Extension-side**: Until the pool interface is extended, `SwapAllowlistExtension` should document that it is incompatible with router-mediated swaps and revert if `sender` is a known router, or require that pools using this extension only accept direct `pool.swap` calls.

The analogous fix in the external report is adding a constraint that the actual data size equals the declared message size; here the analogous constraint is that the checked actor equals the actual economic actor.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension in beforeSwap order.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)      // allow standard periphery
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true  →  check passes.
8. Bob's swap executes despite not being on the allowlist.
```

The `SwapAllowlistExtension` returns `IMetricOmmExtensions.beforeSwap.selector` and the pool proceeds to settlement, transferring tokens to Bob. [3](#0-2) [6](#0-5) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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
