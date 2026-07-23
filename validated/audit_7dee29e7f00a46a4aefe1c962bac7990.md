### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and forwards the router address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the original end-user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value as the `sender` argument in the ABI-encoded call to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. The original user's identity is never visible to the extension.

This creates two mutually exclusive failure modes for any pool that configures `SwapAllowlistExtension`:

| Router allowlist state | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| Router **not** allowlisted | **Blocked** (broken UX) | Blocked |
| Router **allowlisted** | Allowed | **Allowed (bypass!)** |

The bypass path is the fund-impacting case: once the pool admin allowlists the router to let legitimate users trade through it, every unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the same pool and the extension passes unconditionally.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated pool — its purpose is to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, protocol-internal actors, or whitelisted market makers). Once the router is allowlisted (the only way to let legitimate users trade through the standard periphery), the curation is entirely defeated. Any address can execute swaps at oracle-anchored prices against the pool's LP reserves, draining value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user who can call `MetricOmmSimpleRouter` can exploit this. The precondition — the router being allowlisted — is the natural operational state for any pool that wants to support the standard periphery swap path. Pool admins have no way to simultaneously allow router-mediated swaps for legitimate users and block non-allowlisted users from using the same router, so the bypass is an unavoidable consequence of enabling router support.

---

### Recommendation

The extension must check the **original end-user's identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and verifies it. This requires the router to be trusted to populate the field honestly, which is acceptable since the router is a protocol-controlled contract.

2. **Check `sender` against a trusted-router registry**: The extension maintains a set of trusted routers. When `sender` is a trusted router, the extension reads the original user from a router-exposed view (e.g., a transient-storage slot the router populates before calling the pool). This keeps the extension self-contained.

Either approach ensures the allowlist gates the economically relevant actor rather than the intermediary contract.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists `alice` and the router (to let alice trade via router).
swapAllowlist.setAllowedToSwap(address(pool), alice, true);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Mallory is NOT allowlisted.
// Mallory calls the router — the pool sees msg.sender = router, which IS allowlisted.
vm.prank(mallory);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: mallory,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
