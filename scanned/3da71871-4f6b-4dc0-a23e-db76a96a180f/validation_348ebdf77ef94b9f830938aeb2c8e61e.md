### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass a pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every unprivileged caller can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][pool's msg.sender]`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The router does not forward the original caller's identity anywhere in the swap path; `extensionData` is user-controlled but `SwapAllowlistExtension` never reads it: [3](#0-2) 

This creates an irresolvable dilemma for the pool admin:

- **Router not allowlisted**: allowlisted users cannot use the router at all — the allowlist blocks them.
- **Router allowlisted**: every unprivileged user can bypass the allowlist by calling any router entry-point, because the extension sees only the router address.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

Note the asymmetry with `DepositAllowlistExtension`, which correctly ignores `sender` and checks `owner` (the position owner — the economically relevant party): [5](#0-4) 

`SwapAllowlistExtension` has no equivalent design; it checks the immediate caller, which is the router for any router-mediated swap.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional traders, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged address can trade in the pool by calling `MetricOmmSimpleRouter`. LPs in such a pool are exposed to adverse selection from actors the pool admin explicitly intended to exclude, leading to direct LP principal loss through unfavorable swap execution against non-allowlisted counterparties.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with tokens can call `MetricOmmSimpleRouter.exactInputSingle`. The precondition — the router being allowlisted — is the natural and necessary step any pool admin takes when they want their allowlisted users to be able to use the standard periphery router. The bypass is therefore reachable in every realistic production deployment of a swap-allowlisted pool that also supports router access.

---

### Recommendation

The extension must identify the actual user, not the immediate pool caller. Two approaches:

1. **Check `recipient` instead of `sender`** — for swaps the recipient is the address that receives output tokens and is user-specified. This is analogous to how `DepositAllowlistExtension` checks `owner`. The extension would gate who can receive swap proceeds, which is the economically relevant identity.

2. **Decode the actual user from `extensionData`** — the router already forwards `params.extensionData` unchanged to the pool. The extension can require the caller to ABI-encode their address in `extensionData` and verify `ecrecover` or a signed permit, giving a cryptographically verified user identity even when the immediate caller is the router.

Option 1 is simpler and consistent with the deposit allowlist design:

```solidity
// In SwapAllowlistExtension.beforeSwap, replace:
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
// with:
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router.
3. Non-allowlisted `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true`.
6. The swap executes successfully for `attacker`, bypassing the allowlist entirely.

Conversely, if the admin does **not** allowlist the router:

3. Allowlisted `user` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Pool sees `msg.sender = router`; extension checks `allowedSwapper[pool][router]` → `false`.
5. Revert: `NotAllowedToSwap` — the allowlisted user cannot use the router. [3](#0-2) [1](#0-0) [4](#0-3)

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
