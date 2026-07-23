### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to support router-mediated swaps, every unpermissioned user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the `sender` parameter to every configured extension. `SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

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
```

When this executes, `pool.swap()` sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

This creates an irresolvable dilemma for any pool admin who configures a swap allowlist:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken core functionality |
| Router **allowlisted** | Every unpermissioned user bypasses the allowlist by routing through the public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified market makers, institutional counterparties, or whitelisted strategies) can be fully bypassed by any public user calling `MetricOmmSimpleRouter.exactInputSingle()`, `exactInput()`, or `exactOutputSingle()`. The attacker trades on a pool they are not permitted to access, extracting value at the oracle-anchored price without the pool admin's authorization. This is a direct loss of curation control and potentially of LP assets if the restricted pool carries favorable pricing.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any EOA or contract can call it with any pool address. No special privilege, flash loan, or multi-step setup is required. The bypass is reachable on every swap on every allowlisted pool whose admin has also enabled router access.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate dispatcher. Two sound approaches:

1. **Encode the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. The extension must reject calls where `extensionData` is empty or the encoded address is not allowlisted.

2. **Check `sender` against a router-aware allowlist**: Extend the allowlist to a two-level mapping `allowedSwapper[pool][router][user]` and require the router to forward the user address through a trusted channel (e.g., a signed payload or transient storage pattern similar to the liquidity adder's pay-context).

The simplest safe fix is to prohibit router-mediated swaps on allowlisted pools entirely by never allowlisting the router address, and documenting that allowlisted pools must be accessed via direct `pool.swap()` calls only.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for allowlisted users.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` — `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Attacker's swap executes on the restricted pool without being on the allowlist.

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
