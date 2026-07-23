### Title
SwapAllowlistExtension checks router address instead of original user, allowing allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks the router's allowlist status rather than the original user's. If the router is allowlisted (which is required for any allowlisted user to use it), every non-allowlisted user can bypass the gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument the pool forwards. The pool's `swap` entry-point has no explicit `sender` parameter: [2](#0-1) 

So the pool uses its own `msg.sender` — the direct caller — as `sender` when it invokes `_beforeSwap`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap(...)`, the pool's `msg.sender` is the router: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`.

The pool admin faces an inescapable dilemma:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken core functionality |
| Router **allowlisted** | Every non-allowlisted user bypasses the gate by routing through the router |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

The same structural problem exists in `DepositAllowlistExtension`, which gates `beforeAddLiquidity` by the `owner` argument. When `MetricOmmPoolLiquidityAdder` is used, the checked identity may again diverge from the economically relevant depositor. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., for institutional market-makers with favorable oracle-anchored pricing) and configures `SwapAllowlistExtension` to restrict access to approved addresses cannot enforce that restriction when the router is in use. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity at the same oracle-derived bid/ask prices the admin intended only for approved counterparties. This constitutes a direct admin-boundary break with fund-impacting consequences: unauthorized traders extract value from LP positions at prices the LPs agreed to provide only to specific parties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly documented swap entry-point for the protocol. Any user aware of the allowlist restriction can trivially route through the router instead of calling the pool directly. No privileged access, special tokens, or unusual market conditions are required. The trigger is a single standard router call.

---

### Recommendation

The pool must pass the **original initiating user** — not its own `msg.sender` — as `sender` to extension hooks. Two viable approaches:

1. **Caller-supplied sender with callback verification**: Add an explicit `sender` parameter to `pool.swap()` and verify it matches the address that pays in the swap callback (analogous to how Uniswap v4 passes `msgSender` through the unlock path).

2. **Extension reads callback context**: Have the router store the original `msg.sender` in a transient slot before calling the pool, and have the extension read that slot directly (similar to how the router already uses `_setNextCallbackContext` for payment routing).

Until fixed, pool admins should not rely on `SwapAllowlistExtension` for security-critical access control when the router is deployed.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades in a pool he was explicitly excluded from, at oracle-anchored prices intended only for Alice. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
