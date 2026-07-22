Based on my investigation of the codebase, I've identified a valid analog vulnerability.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **actual user**. If the router is allowlisted (or `allowAllSwappers` is set to accommodate router users), any non-allowlisted user can bypass the curated pool's access control entirely by routing through the router.

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this directly to the extension:

```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct namespace) and `sender` is the first argument — which is the **router's address**, not the end user's address, when the swap is router-mediated.

A pool admin who wants to allow router-mediated swaps for allowlisted users must allowlist the router contract itself. But allowlisting the router grants **all users** the ability to swap through it, because the extension cannot distinguish between different users behind the router. The allowlist is therefore either:

1. **Bypassed entirely** — if the router is allowlisted, any non-allowlisted user can swap by routing through `MetricOmmSimpleRouter`.
2. **Over-restrictive** — if the router is not allowlisted, legitimate allowlisted users who use the router are blocked.

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users while blocking non-allowlisted users.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled addresses) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can trade freely on the pool, extracting value from LP positions that were priced assuming a controlled counterparty set. This is a direct loss of LP principal through bad-price execution against an unauthorized swapper.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint in the periphery layer. Any user who discovers the allowlist restriction on a direct pool call will naturally attempt the router path. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single router call suffices. Pools that deploy `SwapAllowlistExtension` are precisely the pools where this matters most.

### Recommendation

The `SwapAllowlistExtension` should check the **original user** rather than the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should accept a `swapper` parameter and pass it as `callbackData` or a dedicated field so the extension can recover the true initiator. The pool would need to expose this identity through the hook arguments.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the economic beneficiary, the allowlist could gate on `recipient`. However, this may not hold for all swap configurations.

The cleanest fix is to have the router forward the original `msg.sender` as an authenticated identity field that the extension can verify, similar to how Uniswap v4 hooks receive `tx.origin` or a signed permit for identity binding.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, routerAddress, true)` to enable router-mediated swaps for allowlisted users.
3. Non-allowlisted user `attacker` calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `attacker` successfully swaps on a pool they were never authorized to access.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
