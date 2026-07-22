### Title
SwapAllowlistExtension Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Allowlist-Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. The pool always sets `sender = msg.sender` of the `swap` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way to enable router-mediated swaps on a restricted pool), the per-user allowlist is completely bypassed and any user can swap.

---

### Finding Description

**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller, not the end user
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

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
  )
);
```

**Guard checks the wrong identity:**

`SwapAllowlistExtension.beforeSwap` uses the received `sender` to look up the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct for pool-identity). `sender` is the address the pool forwarded — the router.

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 71-80
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

`msg.sender` to the pool is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same applies to `exactInput` (all hops call `pool.swap` with `msg.sender = router`) and `exactOutput` (recursive callback hops also call `pool.swap` with `msg.sender = router`).

**The forced dilemma for pool admins:**

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all; must call the pool directly |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user access control.

**Contrast with `DepositAllowlistExtension` (not affected):**

The deposit extension checks `owner`, not `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`owner` is the explicit position-owner argument passed by the caller, not `msg.sender`, so the liquidity adder correctly gates the actual beneficiary. The swap extension has no equivalent — it only receives `sender` (the immediate caller).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist guard — the only mechanism preventing unauthorized swaps — is rendered inoperative for all router-mediated paths. Unauthorized users can drain pool liquidity at oracle-quoted prices, causing direct loss of LP principal.

---

### Likelihood Explanation

The router is the standard user-facing entry point for the protocol. Any pool admin who wants their pool to be usable via the router must allowlist the router address. This is the expected operational pattern. Once the router is allowlisted, the bypass is unconditional and requires no special privileges, timing, or tokens beyond a normal swap call.

---

### Recommendation

1. **Pass the originating user through `extensionData`**: The router should encode `msg.sender` (the end user) into `extensionData` and the extension should decode and verify it. This requires a convention between router and extension.

2. **Add a `payer`/`originator` field to the swap interface**: Extend `IMetricOmmPoolActions.swap` with an explicit `originator` address (analogous to `owner` in `addLiquidity`) that the pool forwards to extensions instead of `msg.sender`. The router would pass `msg.sender` (the user) as `originator`.

3. **Short-term mitigation**: Document that `SwapAllowlistExtension` gates the immediate caller, not the end user, and that pools using it must not allowlist the router if per-user access control is intended.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens

Result:
  - attacker bypassed the per-user allowlist
  - allowedSwapper[pool][attacker] was never set to true
  - The guard checked the router's allowlist entry, not the attacker's
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
