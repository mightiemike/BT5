Looking at the `SwapAllowlistExtension`, the pool's `swap` function, and the `MetricOmmSimpleRouter`, I can trace the exact identity mismatch.

**Pool passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

**ExtensionCalling forwards that `sender` verbatim:** [2](#0-1) 

**SwapAllowlistExtension checks `sender` (the pool's `msg.sender`) against the allowlist:** [3](#0-2) 

**Router calls `pool.swap()` with itself as `msg.sender`, never forwarding the real user:** [4](#0-3) 

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the natural configuration for usability), every user on the network can bypass the per-user restriction.

### Finding Description
The call chain for a router-mediated swap is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

Inside `beforeSwap`:

```solidity
// msg.sender = pool, sender = router (NOT the real user)
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

The real user's address is stored in transient storage by the router for the payment callback, but it is never forwarded to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This produces two symmetric failures:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user bypasses the per-user restriction; the allowlist is inert |
| Router **is not** allowlisted | Every individually-allowlisted user is blocked when using the router |

The first case is the critical one. A pool admin who allowlists the router for normal operation (the expected production configuration) inadvertently opens the pool to all users, defeating the entire purpose of the extension.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` parameter (the LP position owner passed explicitly by the caller), not `sender`. [5](#0-4) 

### Impact Explanation
A pool protected by `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., trusted market makers, KYC'd counterparties, or protocol-internal actors). Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's liquidity at live oracle prices. This exposes LP assets to unrestricted arbitrage and draining by actors the pool admin explicitly intended to exclude, constituting a direct loss of LP principal.

### Likelihood Explanation
The router is a public, permissionless periphery contract. For any pool that wants to be usable through the standard router, the pool admin must allowlist the router. This is the default operational path; the bypass is therefore reachable by any user on every router-enabled allowlisted pool. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

### Recommendation
The extension must check the identity of the economic actor, not the intermediary. Two sound approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Check `sender` only for direct pool calls; require a trusted forwarder pattern**: Extensions that need per-user gating should require callers to be EOAs or use EIP-2771-style trusted forwarder metadata so the real originator is always recoverable.

The simplest immediate fix is to document that `SwapAllowlistExtension` gates the *direct caller of `pool.swap`*, not the end user, and that allowlisting the router opens the pool to all users — then provide a router-aware variant that decodes the real user from `extensionData`.

### Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes against LP liquidity at oracle prices, with no allowlist check on the attacker's address.

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
