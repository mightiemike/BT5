### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router (the natural step to let allowlisted users use the router), any unprivileged user can bypass the curated-pool allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` of `pool.swap` is the router contract, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` (the intermediary) and checks `owner` (the economic actor): [5](#0-4) 

The deposit extension deliberately skips the first parameter (`sender`) and keys on `owner`. The swap extension has no equivalent — it keys on `sender`, which collapses to the router address for every router-mediated swap.

---

### Impact Explanation

Two mutually exclusive failure modes arise:

**Mode A — Allowlist bypass (High impact):** The pool admin allowlists the router so that allowlisted users can reach the pool through the standard periphery path. Because the check is `allowedSwapper[pool][router]`, every user who calls the router — including users the admin explicitly excluded — passes the guard. The curated pool is effectively open to all.

**Mode B — Broken core functionality (Medium impact):** The pool admin does not allowlist the router. Allowlisted users who call `exactInputSingle` or `exactInput` receive `NotAllowedToSwap` even though they are individually permitted. The router path is permanently unusable for the pool.

Mode A is the direct-loss path: non-allowlisted users trade on a pool whose oracle pricing or liquidity composition was designed for a restricted set of counterparties, potentially extracting value from LPs or violating regulatory/compliance constraints the pool admin intended to enforce.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production extension in the periphery package, deployed alongside pools that require access control.
- A pool admin who wants allowlisted users to use the router will naturally add the router to the allowlist — this is the only way to make the router work for their users.
- Once the router is allowlisted, the bypass requires no special privilege: any user calls `exactInputSingle` with the target pool.
- The router is a single shared contract, so allowlisting it once opens the pool to the entire public.

---

### Recommendation

Key the allowlist on the actual economic actor, not the intermediary. Two concrete options:

1. **Decode user identity from `extensionData`:** Have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension` decode and check that address. This requires a standardized encoding convention between the router and the extension.

2. **Check `recipient` as a proxy for the economic actor:** For most swap flows the recipient is the user. This is imperfect (recipient can be a third-party address) but eliminates the router-address collapse.

The minimal safe fix mirrors the deposit extension pattern: ignore the intermediary (`sender`) and check the field that identifies the party who economically benefits from the action.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (the second call is required for Alice to use the router).
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. Bob's swap executes successfully on the curated pool despite never being allowlisted.

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
