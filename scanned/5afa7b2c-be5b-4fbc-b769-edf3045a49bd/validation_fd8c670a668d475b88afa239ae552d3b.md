### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every non-allowlisted user can bypass the gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first argument (`sender`) to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The pool sees `msg.sender` = router, so `sender` forwarded to the extension = router address. The router stores the original `msg.sender` only in transient storage for callback settlement; it is never passed to the pool or the extension: [5](#0-4) 

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the gate via the router |

Contrast with `DepositAllowlistExtension`, which correctly ignores `sender` (the `LiquidityAdder` intermediary) and checks `owner` (the actual position beneficiary): [6](#0-5) 

The swap extension has no equivalent correct binding — it checks the intermediary, not the end-user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The bypassing user executes a live swap against pool liquidity at oracle prices, receiving real output tokens. This is a direct, fund-impacting bypass of a core pool access-control guard.

---

### Likelihood Explanation

The router is the standard user-facing entry point documented and deployed alongside the pool. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the natural, expected configuration. The bypass is therefore reachable on any allowlisted pool that supports router usage, which is the common case.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`** if the intent is to restrict who receives output tokens (analogous to how `DepositAllowlistExtension` checks `owner`).
2. **Have the router encode the original `msg.sender` into `extensionData`** in a verifiable way (e.g., signed or authenticated), and have the extension decode and verify it. This requires a coordinated protocol-level convention.

The simplest correct fix that mirrors `DepositAllowlistExtension`'s pattern is to gate on `recipient` (the address that actually receives the swapped tokens), which the router always sets to the user-supplied destination and cannot spoof to bypass a per-address gate.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `BEFORE_SWAP_ORDER` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — router is allowlisted so alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` with himself as `recipient`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender` = router.
6. Pool calls `_beforeSwap(router, bob, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives output tokens despite never being allowlisted.

Direct call by Bob (`pool.swap(bob, ...)`) would correctly revert because `allowedSwapper[pool][bob]` = `false`. The router path silently substitutes the router's allowlisted identity for Bob's non-allowlisted one — the exact account-ordering misbinding described in the seed report.

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
