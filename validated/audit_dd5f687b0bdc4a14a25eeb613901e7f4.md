Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates, `msg.sender` at the pool is the router contract address, not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for curated users simultaneously opens the gate to every unprivileged caller, because any user can call the public router and have their swap attributed to the allowlisted router address.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the per-pool namespace key) and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to thread the originating user's address into the `sender` slot seen by the extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The router is a fully public contract with no access control of its own — any EOA can call any of these functions. When the pool admin allowlists the router address (a natural step to let curated users access the standard periphery UX), the check `allowedSwapper[pool][router] == true` passes for every caller who routes through the router, regardless of whether the originating EOA is allowlisted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the LP position recipient) rather than `sender` (the immediate caller), because `removeLiquidity` enforces `msg.sender == owner`, anchoring the check to the economically relevant actor: [5](#0-4) 

No equivalent anchor exists for swaps — the economic beneficiary is `recipient`, not `sender`, yet the allowlist is keyed on `sender`.

The existing test suite only tests direct pool calls via `TestCaller` contracts and never exercises the router path against an allowlisted pool, so this bypass is not caught by any existing test: [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd market participants, whitelisted institutions) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker executes swaps at the oracle-derived bid/ask price against LP reserves that were deposited under the assumption that only allowlisted parties could trade. LP principal is directly at risk: the attacker can drain whichever side of the pool the oracle price makes favorable, and the LPs have no recourse because the pool accepted the swap as valid. This matches the **Broken core pool functionality causing loss of funds** and **Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path** impact categories.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router — a natural and expected operational step for any pool that wants its curated users to access the standard periphery UX. The router is the canonical swap entry point. A pool admin who allowlists individual users and then also allowlists the router to give those users router access has unknowingly opened the gate to everyone. No privileged action, malicious setup, or non-standard token is required on the attacker's side. The attack is repeatable and requires no special capability beyond calling a public function.

## Recommendation
The `SwapAllowlistExtension` must gate the **originating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension verifies it against the allowlist (optionally with a signature to prevent spoofing). This requires a coordinated change to the router and the extension.

2. **Deploy a router-aware extension**: Deploy an extension that reads the payer stored in the router's transient callback context (`_getPayer()`) and checks that address against the allowlist. This couples the extension to the specific router implementation but requires no change to the pool or router call path.

The cleanest fix is option 1 with a signed payload so the extension can cryptographically verify the originating user regardless of which intermediary called the pool.

## Proof of Concept
```
Setup:
  - Pool P deployed with SwapAllowlistExtension (extension2, beforeSwap order = 2)
  - Pool admin calls swapExtension.setAllowedToSwap(P, alice, true)
  - Pool admin calls swapExtension.setAllowedToSwap(P, router, true)
    (to let alice use the router)
  - Alice and LPs deposit liquidity

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: P, recipient: bob, ...})
  2. Router calls P.swap(bob, zeroForOne, amount, limit, "", extensionData)
     → msg.sender at pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[P][router] → true → PASS
  5. Swap executes at oracle price; Bob receives output tokens from LP reserves
  6. LPs suffer loss; Bob profits

Expected behavior: Bob's swap should revert NotAllowedToSwap.
Actual behavior: Bob's swap succeeds because the router is allowlisted.

Foundry test plan:
  - Extend FullMetricExtension.t.sol to deploy MetricOmmSimpleRouter
  - Allowlist alice and the router address on pool P
  - Have bob (not allowlisted) call router.exactInputSingle targeting pool P
  - Assert the swap succeeds (demonstrating the bypass) rather than reverting NotAllowedToSwap
```

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
