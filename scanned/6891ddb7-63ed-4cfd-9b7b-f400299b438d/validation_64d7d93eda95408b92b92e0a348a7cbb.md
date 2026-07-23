### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the router is allowlisted (the only way to permit router-mediated swaps), every non-allowlisted user can bypass the swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the actual user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This creates an irresolvable dilemma for pool admins:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the gate via the router |

The `DepositAllowlistExtension` does not share this flaw — it checks `owner` (the position owner explicitly passed by the caller), which correctly identifies the economically relevant party regardless of who calls `addLiquidity`: [5](#0-4) 

The asymmetry confirms the swap-side check is wrong: deposits gate the owner; swaps gate the intermediary.

---

### Impact Explanation

Any non-allowlisted address can trade in a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The swap allowlist — the only on-chain mechanism to restrict who may trade against LP funds — is rendered ineffective the moment the router is added to the allowlist. Restricted pools (e.g., institutional-only, regulatory-gated, or partner-only pools) lose their access control entirely, exposing LP assets to unauthorized counterparties.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs to support router-mediated swaps for its allowlisted users must allowlist the router. This is the normal operational path. Once the router is allowlisted, the bypass is available to every address on-chain with no special privileges, no admin cooperation, and no non-standard tokens required.

---

### Recommendation

The extension must gate the **actual user**, not the intermediary. Two viable approaches:

1. **Forward the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user; however this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Dedicated router-aware allowlist**: Extend the extension interface to accept an `actualUser` field that the router populates, verified by a signature or trusted-forwarder pattern.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` as the first word of `extensionData` for allowlist-gated pools, and the extension decodes and checks that address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // Alice is the intended allowlisted user
  allowedSwapper[pool][router] = true   // admin adds router so Alice can use it
  allowedSwapper[pool][bob]    = false  // Bob is NOT allowlisted

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check passes
    → Bob's swap executes against LP funds in the restricted pool
```

Bob has bypassed the swap allowlist with no special privileges, exploiting the fact that `sender` is the router address rather than the actual user.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
