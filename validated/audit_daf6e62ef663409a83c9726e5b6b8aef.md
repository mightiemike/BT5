Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via the router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the intermediary router address as `sender` rather than the originating EOA, because `MetricOmmPool.swap` passes `msg.sender` (the router) into `_beforeSwap`. When a pool admin allowlists the router to enable router-mediated swaps for legitimate users, the allowlist is completely bypassed for every caller of the router. No configuration exists that simultaneously restricts swaps to specific users and permits those users to use the public router.

## Finding Description
**Call path:**

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the router is `msg.sender` to the pool. [1](#0-0) 

2. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` = router address. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension hook. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router — never the originating EOA. [4](#0-3) 

**The irresolvable dilemma:**
- If the admin does **not** allowlist the router: allowlisted EOAs cannot use the router (router address fails the check).
- If the admin **does** allowlist the router: `allowedSwapper[pool][router] == true` passes for every caller of the router, including completely unauthorized users.

**Secondary confirmed flaw — `DepositAllowlistExtension`:** The hook ignores the `sender` parameter entirely and checks `owner` instead. [5](#0-4) 

`_validateOwner` in `MetricOmmPoolLiquidityAdder` only rejects `address(0)` — it does not enforce `owner == msg.sender`. [6](#0-5) 

An unprivileged caller can pass any allowlisted address as `owner`, causing the extension to approve the deposit while the caller's tokens are pulled and LP shares are minted to the allowlisted address — bypassing the deposit allowlist. [7](#0-6) 

## Impact Explanation
Any EOA can execute swaps on a pool whose admin intended to restrict access to a specific set of addresses. The allowlist — the sole access-control mechanism on the swap path — is rendered ineffective the moment the router is allowlisted. Unauthorized swaps drain pool liquidity at oracle-derived prices, directly reducing LP principal and owed fees. This constitutes broken core pool functionality and direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation
The trigger is a standard, expected admin action: allowlisting the router so that approved users can interact via the periphery. Any pool that deploys `SwapAllowlistExtension` and also wants router support will hit this condition. No privileged attacker capability is required; any EOA can call `MetricOmmSimpleRouter.exactInputSingle`. The condition is deterministic and repeatable.

## Recommendation
The extension must gate on the actual end user, not the intermediary:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and verifies this value. This is acceptable since the router is a known, audited periphery contract.
2. **Check `recipient` instead of `sender`:** For swap allowlists, gating on the output recipient is often the economically relevant identity and requires no protocol changes.

For `DepositAllowlistExtension`, change the check to use the `sender` parameter (the actual caller of `pool.addLiquidity`) rather than `owner`, or enforce `sender == owner` in the liquidity adder before forwarding to the pool.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension on beforeSwap
  allowedSwapper[pool][alice] = true   // alice is the only approved swapper
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender in pool = router

  pool calls _beforeSwap(msg.sender=router, ...)
  extension receives: sender = router
  check: allowedSwapper[pool][router] == true → PASSES

  bob's swap executes successfully despite not being allowlisted.
  alice's exclusive access is defeated.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, call `router.exactInputSingle` from `bob`, assert the swap succeeds and bob receives output tokens.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
