### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`, and `MetricOmmSimpleRouter` is the immediate caller for all router-mediated swaps, the allowlist checks whether the **router** is allowlisted — not the actual end-user. Any user can bypass a per-user swap allowlist by routing through the public `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...)          [msg.sender = router]
     → _beforeSwap(msg.sender=router, …)
     → SwapAllowlistExtension.beforeSwap(sender=router, …)
     → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` to the pool: [4](#0-3) 

This creates an irresolvable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for individually allowlisted users |
| Router **allowlisted** | Every user on the network can bypass the per-user allowlist by calling the router |

The `DepositAllowlistExtension` does **not** share this flaw — it checks the `owner` argument (the actual position owner), not `sender` (the caller): [5](#0-4) 

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-controlled addresses). To allow those users to swap via the router, the admin must allowlist the router. Once the router is allowlisted, **any** unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's LP positions. The allowlist guard is completely inoperative. Unauthorized swappers can extract value from LP positions through swaps the pool was designed to prohibit, constituting a broken core pool functionality with direct LP fund impact.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entry point; legitimate allowlisted users will naturally use it.
- The admin must allowlist the router to make the pool usable for any router-mediated swap, making the bypass condition the default operational state.
- No special privileges, tokens, or setup are required — any EOA or contract can call `exactInputSingle`.
- The bypass is silent: no revert, no event distinguishing the actual user from the router.

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, do not rely on the `sender` argument for the allowlist check. Instead, require callers to supply the actual end-user address in `extensionData` and verify it, or add a dedicated `tx.origin` fallback for EOA-only pools. Alternatively, document that the extension is incompatible with router-mediated swaps and enforce this at the factory level.

**Long term:** Redesign the `sender` semantics in the hook interface so that the pool propagates the original initiator (e.g., via transient storage or an explicit `originator` field) rather than `msg.sender`, mirroring how `DepositAllowlistExtension` correctly gates `owner` rather than the immediate caller.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so legitimate users can swap.
// Eve (not individually allowlisted) calls the router directly:

router.exactInputSingle(ExactInputSingleParams({
    pool:            address(allowlistedPool),
    tokenIn:         token0,
    recipient:       eve,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// pool.swap is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Eve's swap executes successfully despite not being individually allowlisted.
```

The check that should block Eve — `allowedSwapper[pool][eve]` — is never evaluated. The guard always passes for any user routing through the allowlisted router, directly analogous to the RUSTSEC-2020-0019 pattern where the expected termination condition always returns true because the wrong state is inspected.

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
