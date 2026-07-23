### Title
`SwapAllowlistExtension::beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the allowlist checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any user can bypass the swap allowlist by routing through the public router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter::exactInputSingle(params)`. [1](#0-0) 

2. The router calls `pool.swap(params.recipient, ...)` — the pool's `msg.sender` is the **router address**. [2](#0-1) 

3. Inside `MetricOmmPool::swap`, the pool calls `_beforeSwap(msg.sender, ...)` — so `sender = address(router)`. [3](#0-2) 

4. `ExtensionCalling::_beforeSwap` forwards `sender` (the router) to the extension's `beforeSwap`. [4](#0-3) 

5. `SwapAllowlistExtension::beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the **router**, not the actual user. [5](#0-4) 

The allowlist is keyed as `allowedSwapper[pool][swapper]` and is intended to gate individual swapper addresses: [6](#0-5) 

But the check at line 37 receives the router as `sender`, so the guard evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through the router: [7](#0-6) 

**Two broken outcomes:**

- **Bypass (primary impact):** If the pool admin allowlists the router address (the natural operational choice so that allowlisted users can use the router), every unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and pass the guard, because the guard sees `allowedSwapper[pool][router] == true`.

- **Lockout (secondary impact):** If the pool admin does not allowlist the router, then even individually allowlisted users cannot swap through the router — the guard sees `allowedSwapper[pool][router] == false` and reverts with `NotAllowedToSwap`, breaking the expected periphery UX.

The same identity mismatch applies to all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and to the recursive `_exactOutputIterateCallback` path, which also calls `pool.swap` with `msg.sender = router`. [8](#0-7) 

The existing integration test (`FullMetricExtensionTest`) only exercises direct pool calls, never router-mediated swaps, so the bypass is untested: [9](#0-8) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or a private OTC pool) is fully bypassed by any user who calls the public `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves; the pool's LPs bear the economic exposure of an unauthorized swap at oracle-derived prices. This constitutes broken core pool functionality and direct loss of LP principal through unauthorized swap settlement.

### Likelihood Explanation

**High.** The router is a public, permissionless contract. No special role, token balance, or prior interaction is required. Any user who can call `exactInputSingle` can exploit this. The router is the canonical entry point documented for end users, so the attack surface is the default usage path.

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the immediate pool caller. Two options:

1. **Check `sender` against the allowlist only for direct pool calls; require the router to forward the original user identity in `extensionData`.** The extension decodes the real user from `extensionData` when `sender` is a known router.

2. **Gate on `recipient` instead of `sender` for router flows**, or require the router to pass the original `msg.sender` as a verified field in `extensionData` that the extension validates (e.g., signed or via a trusted forwarder pattern).

The simplest correct fix is to have the router pass the original `msg.sender` inside `extensionData` and have the extension decode and check that address when the immediate `sender` is a recognized router. Alternatively, the pool's `swap` interface could be extended to carry an explicit `originator` field that the pool populates from a trusted transient-storage context set by the router before the call.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that allowlisted users can use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls the router directly:
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// Succeeds: pool sees sender = address(router), allowedSwapper[pool][router] == true.
// Attacker receives token1 output from LP reserves without being on the allowlist.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
