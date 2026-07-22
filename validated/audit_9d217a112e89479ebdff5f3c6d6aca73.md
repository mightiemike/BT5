### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via the Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes that `msg.sender`, so the allowlist checks the router address rather than the actual end user. Because the router is a public, permissionless contract, any non-allowlisted user can bypass the swap allowlist by routing through it.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its gate as follows: [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. The pool populates `sender` from its own `msg.sender` — the direct caller of `pool.swap()`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant), the router calls `pool.swap()` on the user's behalf. At that point, `msg.sender` inside the pool is the **router address**, not the end user. The pool therefore passes the router address as `sender` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router]`.

For legitimate router-based swaps to work on an allowlisted pool, the pool admin must add the router to the allowlist. But `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it. Once the router is allowlisted, every non-allowlisted user can route through it and the allowlist is entirely defeated.

The same structural issue exists for `DepositAllowlistExtension`: it ignores the `sender` (first parameter, the actual payer/caller) and gates only on `owner`. [3](#0-2) 

A non-allowlisted user can call `MetricOmmPoolLiquidityAdder.addLiquidity*` and supply an allowlisted address as `owner`; the extension passes because it never inspects who is actually paying or initiating the call.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties provides no real restriction once the router is allowlisted. Any unprivileged user can execute swaps against the pool, draining LP value at oracle-quoted prices, extracting fees, or front-running in ways the pool admin explicitly intended to prevent. This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized swaps at live oracle prices against LP capital).

### Likelihood Explanation

The router is the standard user-facing entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps for its allowlisted users must allowlist the router. This is the expected operational configuration, making the bypass reachable by any unprivileged caller in normal deployment.

### Recommendation

The extension must verify the **end user**, not the immediate pool caller. Two approaches:

1. **Pass the originating user through extensionData**: The router should encode the real `tx.origin` or a signed user identity in `extensionData`, and the extension should decode and verify it. This requires router cooperation but preserves composability.
2. **Check `tx.origin` as a fallback**: When `sender` is a known router/intermediary, fall back to `tx.origin`. This is simpler but has its own trust assumptions.
3. **Require direct pool interaction**: Document that allowlisted pools must not allowlist the router, and users must call `pool.swap()` directly. This breaks router composability but closes the bypass.

The deposit allowlist should additionally check `sender` (the payer/initiator), not only `owner`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)  // necessary for router users
  - Alice (non-allowlisted) wants to swap

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=Alice, ...)
  3. pool.swap() sets sender = address(router)
  4. _beforeSwap passes sender=router to SwapAllowlistExtension
  5. Extension checks allowedSwapper[pool][router] → true
  6. Swap executes at live oracle price — Alice's swap succeeds
  7. Allowlist is bypassed with zero privilege
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
