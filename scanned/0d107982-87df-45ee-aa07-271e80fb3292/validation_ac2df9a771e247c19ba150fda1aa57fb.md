### Title
`SwapAllowlistExtension` gates by the direct `pool.swap()` caller (the router), not the end user — allowlisting the router opens the curated pool to all swappers - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual end user. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address — but doing so opens the pool to **every** user, defeating the per-user curation policy entirely.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` checks the wrong actor when the router is involved.**

The extension gates by `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (the extension caller), and `sender` is the first argument the pool passes to `_beforeSwap`. The pool sets that argument to `msg.sender` of the `pool.swap()` call, as wired in `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` is called by an end user, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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
``` [3](#0-2) 

The pool receives `msg.sender = router_address`. It passes `sender = router_address` to `_beforeSwap`. The extension therefore checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][end_user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, so `sender` is always the router.

**The dilemma for the pool admin:**

| Admin configuration | Effect |
|---|---|
| Allowlist only specific users (not the router) | Allowlisted users **cannot** use the router; router swaps revert for everyone |
| Allowlist the router | **All** users can swap through the router; per-user curation is defeated |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The existing test confirms the actor checked is the direct `pool.swap()` caller, not the EOA:

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L70
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
// callers[0] is the TestCaller contract that calls pool.swap(), not users[0] (the EOA)
``` [4](#0-3) 

---

### Impact Explanation

**High.** A non-allowlisted user can execute swaps on a pool whose admin intended to restrict trading to a curated set of addresses. The bypass is unconditional once the router is allowlisted: any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the router (allowlisted) rather than the caller (not allowlisted). This breaks the core curation invariant of the `SwapAllowlistExtension` and allows unauthorized parties to trade against pool liquidity.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected configuration: any pool that wants its allowlisted users to be able to use the standard periphery router must allowlist it. The scenario is not exotic; it is the normal production setup for a curated pool that still wants to support the router UX.

---

### Recommendation

The extension must verify the **originating user**, not the direct `pool.swap()` caller. Two viable approaches:

1. **Pass the originator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to encode the correct originator (which it is, as a protocol-controlled contract).

2. **Add an explicit `originator` field to the swap hook interface:** The pool passes both `sender` (direct caller) and an `originator` (set by the caller via a separate argument or transient storage). The extension gates on `originator`.

The `DepositAllowlistExtension` does not share this problem because it gates on `owner` (the position beneficiary explicitly passed by the caller), not on `sender` (the direct pool caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [5](#0-4) 

The swap extension should adopt the same pattern — gate on the economically relevant actor, not the intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, allowedUser, true)` — only `allowedUser` is intended to swap.
3. Pool admin also calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — necessary for `allowedUser` to use the router.
4. `nonAllowedUser` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `nonAllowedUser` has successfully swapped on a pool they were never meant to access. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
