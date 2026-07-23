### Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool always passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` from the pool's perspective. If the pool admin allowlists the router to enable router-mediated swaps, every user—including non-allowlisted ones—can bypass the curated gate by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
  extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first argument) is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
``` [3](#0-2) 

From the pool's perspective, `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router never forwards the original `msg.sender` to the pool.

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` (e.g., to restrict trading to KYC'd counterparties or institutional participants) must choose between two broken states:

1. **Router not allowlisted**: Allowlisted users cannot use the router at all; they must call `pool.swap()` directly. The router—the primary user-facing entry point—is effectively disabled for the pool.

2. **Router allowlisted**: The allowlist is completely bypassed. Any non-allowlisted user calls `router.exactInputSingle(pool, ...)` and the extension sees `sender = router`, which is allowlisted. The curated gate is open to everyone.

In scenario 2, non-allowlisted users can trade on a pool that was designed to exclude them, directly violating the pool's access-control invariant and potentially causing financial or regulatory harm to the pool admin and legitimate LPs.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool admin who configures `SwapAllowlistExtension` and also wants users to use the router will naturally allowlist the router address, triggering the bypass. The attacker needs no special privileges—only the ability to call a public router function with a valid pool address.

---

### Recommendation

The `sender` identity passed through the hook chain must represent the economic actor the pool admin intends to gate, not the intermediate contract. Two options:

1. **Pass the original end-user through the router**: The router could encode the original `msg.sender` in `extensionData` and the extension could decode it. However, this is trust-dependent and easily spoofed by a direct pool caller.

2. **Gate on `recipient` instead of `sender`**: If the pool admin's intent is to restrict who *receives* output tokens, `recipient` is the correct field. For the deposit allowlist, `owner` is already used correctly.

3. **Architectural fix**: The pool should accept an explicit `originator` parameter (set by the router to `msg.sender` before calling the pool) and pass it to extensions as a separate field, distinct from the immediate `msg.sender`. This is the cleanest solution and mirrors how Uniswap v4 handles hook actor identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: swapExtension.setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, ...) — msg.sender = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. extension checks allowedSwapper[pool][router] → true
  5. swap executes; attacker receives output tokens

Result:
  - attacker (not allowlisted) successfully swaps on a curated pool
  - SwapAllowlistExtension.beforeSwap never checked attacker's address
```

The existing test `test_allowedSwapSucceeds` in `FullMetricExtension.t.sol` allowlists `callers[0]` (a `TestCaller` contract that calls the pool directly), confirming the extension works for direct callers but never tests the router path. [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
